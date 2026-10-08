#!/usr/bin/env python3
"""memcurve.py — tree_v2 vs 滚动摘要(+flat RAG) 的遗忘曲线（bonus 实验）。

设计（2026-10-02 起；2026-10-05 加召回）：
- **mode=off（零救援基线）**：探针只读当前（预算内）上下文，不检索、作答不进树。
- **mode=passive（被动召回 / 预检索）**：每轮随当前输入（刚读入的 distractor 批次）自动
  预检索（auto_preretrieve），命中按影子注入组装、命中写回续命——测"跟随工作内容的预检索
  能否顺带捞回事实"。
- **mode=active（主动召回）**：独立一臂——checkpoint 对已从上下文消失的 fact 用其原文定向
  查询救援（命中写回续命）——测"想不起来就去查"的救援率。
- t=0 注入 K 条关键事实（`facts.json`，带独有 token，与 distractor 零碰撞）；随后持续读入
  distractor（ahi-act 剧本句）并老化——tree 走 insert+tick+merge，rolling 走 append+超预算
  中间段摘要压缩 + **无界 flat RAG 索引**（chunk=400 + bge-small-zh 向量，top-k=6，对齐真实
  flat RAG 臂）。
- 公平：两臂同预算（默认 16000 字符）、同 distractor 序列、同检索 top-k；曲线 x 轴 = 累计
  处理字符数。
- 三个 mode 各自独立成臂（命中写回会改变 tree 演化，不能共享同一实例）；rolling 无写回，
  一个实例同时报三档。

用法：
  # 先 mock（零 LLM，秒级，看形状）
  python3 realtest/memcurve/memcurve.py --mock --recall-mode all --n-facts 100 --rounds 80
  # 真实蒸馏（deepseek-flash），三臂
  python3 realtest/memcurve/memcurve.py --recall-mode all --n-facts 100 --rounds 80 \
      --check-every 4 --plot --out realtest/memcurve/out/recall
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"),
          os.path.join(_REALTEST, "editbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from llmclient import make_client                                    # noqa: E402
from realengine import (RealEngine, engine_kwargs,                   # noqa: E402
                        _load_encoder, _empty_mps_cache, EMBED_BATCH)
from textutil import bigrams                                         # noqa: E402
from splitter import split_sentences                                 # noqa: E402


class MockLLM:
    """零 LLM 蒸馏替身：返回内容前 N 字（保留 token 但不做语义取舍），仅供 harness 调试。"""
    model = "mock"

    def chat(self, system, user):
        s = (user or "") if isinstance(user, str) else str(user)
        return "MOCK:" + s[-300:], {"completion_tokens": 0}, 0.0


def make_llm(cfg, mock):
    if mock:
        return MockLLM()
    import copy
    c = copy.deepcopy(cfg)
    c["provider"] = "deepseek"
    c["deepseek"]["temperature"] = 0.0
    return make_client(c)


# ── 语料 ────────────────────────────────────────────────

def load_facts(path):
    return json.load(open(path, encoding="utf-8"))


def synth_facts(n, base):
    """补齐/合成 n 条事实（独有 token）。不足 base 长度时先取 base，再合成。"""
    out = []
    for i in range(n):
        if i < len(base):
            out.append(base[i])
            continue
        k = i + 1
        patterns = [
            ("第 %d 号观测的校验码是 MC-%04d" % (k, k), ["MC-%04d" % k]),
            ("事件 %d 的负责人是 OP-%02d" % (k, k), ["OP-%02d" % k]),
            ("样本 %d 的编号是 SAMPLE-%04d" % (k, k), ["SAMPLE-%04d" % k]),
        ]
        text, keys = patterns[i % 3]
        out.append({"id": "S%03d" % k, "text": text, "keys": keys})
    return out


_HEADER = re.compile(r"^(《|【|人物表|第\d+幕|——|\s*[青白墨蓝]{1}\s*（bench-[a-z]+）\s*$)")


def load_distractors(path):
    doc = open(path, encoding="utf-8").read()
    out = []
    for s in split_sentences(doc):
        s = s.strip()
        if not s or _HEADER.match(s):
            continue
        out.append(s)
    return out


def _hit_text(eng, item):
    if eng._is_atom(item):
        return eng.atoms[item].value or ""
    return eng.nodes[item].value or ""


def _contains(text, fact):
    return any(k in text for k in fact["keys"])


_MODE_KEY = {"off": "kept", "passive": "passive_kept", "active": "active_kept",
             "passive_q": "passiveq_kept", "recover": "passiveq_kept"}


def _eff_kept(tree_res, mode):
    """该 mode 的有效事实保留数：off=基线 in-context；passive/active=召回增强后。"""
    return tree_res.get(_MODE_KEY.get(mode, "kept"), tree_res["kept"])


# ── flat RAG 索引（对齐真实 flat-rag 臂：chunk=400 + bge-small-zh 向量，top-k=6）──

class FlatRAGIndex:
    """rolling 臂的长期记忆：全部历史消息按 chunk 切块、向量检索 top-k。
    无界原始存储（不压缩）——对齐真实 FlatRAGAgent，召回理论上不掉信息。"""

    def __init__(self, chunk_chars=400, top_k=6, min_score=0.2):
        self.chunk_chars = max(1, int(chunk_chars))
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.docs = []          # list[str]
        self.embs = []          # list[vec | None]，与 docs 对齐
        self._encoder = None
        self._encoder_tried = False

    def _enc(self):
        if self._encoder_tried:
            return self._encoder
        self._encoder_tried = True
        try:
            self._encoder = _load_encoder()
        except Exception:
            self._encoder = None
        return self._encoder

    def add(self, text):
        text = (text or "").strip()
        if not text:
            return
        enc = self._enc()
        n = self.chunk_chars
        chunks = [c for c in (text[i:i + n].strip()
                              for i in range(0, len(text), n)) if c]
        if not chunks:
            return
        embs = [None] * len(chunks)
        if enc is not None:
            try:
                embs = list(enc.encode(chunks, normalize_embeddings=True,
                                       batch_size=EMBED_BATCH))
            except Exception:
                embs = [None] * len(chunks)
        for c, e in zip(chunks, embs):
            self.docs.append(c)
            self.embs.append(e)
        _empty_mps_cache()

    def retrieve(self, query):
        """返回 [(score, text)]，top-k（向量优先、无编码器/keyword 降级；min_score 过滤）。"""
        q = (query or "").strip()
        if not q or not self.docs:
            return []
        enc = self._enc()
        scored = []
        if enc is not None:
            try:
                qv = enc.encode(q, normalize_embeddings=True)
                for i, e in enumerate(self.embs):
                    if e is None:
                        continue
                    scored.append((float(e @ qv), i))
            except Exception:
                scored = []
        _empty_mps_cache()
        if not scored:
            qb = bigrams(q)
            if not qb:
                return []
            for i, d in enumerate(self.docs):
                tb = bigrams(d)
                if not tb:
                    continue
                scored.append((len(tb & qb) / max(len(tb), len(qb)), i))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, i in scored[:self.top_k]:
            if enc is not None and score < self.min_score:
                continue
            out.append((score, self.docs[i]))
        return out


# ── tree 臂 ─────────────────────────────────────────────

class TreeArm:
    def __init__(self, cfg, llm, facts, budget=16000, retr=6, recall_mode="off"):
        self.eng = RealEngine(llm, **engine_kwargs(cfg, budget, retr))
        self.budget = budget
        self.retr = retr
        self.recall_mode = recall_mode
        self.facts = facts
        for f in facts:
            self.eng.insert_units([(f["text"], {"source": "fact"})], left_id=self.eng.tail)
        self.fact_ids = [aid for aid, a in self.eng.atoms.items()
                         if (a.metadata or {}).get("source") == "fact"]
        self._fid = {f["id"]: self.fact_ids[i] for i, f in enumerate(facts)}
        self.last_batch = []
        self._passive_shadows = []
        self.recall_active = False   # recover 模式：>阈值后由主循环置 True

    def age(self, sentences):
        for s in sentences:
            self.eng.insert_units([(s, {"source": "distractor"})], left_id=self.eng.tail)
        self.last_batch = list(sentences)
        self.eng.tick(1)
        sig = self.eng.merge_signal()
        if sig == "force":
            self.eng.merge_pass(force_out=True)
        elif sig == "regular":
            self.eng.merge_pass()
        # 被动召回（auto_preretrieve）：随当前输入自动检索并暂存为待注入影子（写回续命）
        if self.recall_mode == "passive":
            q = "\n".join(self.last_batch).strip()
            self._passive_shadows = self.eng.retrieve(q, budget=self.retr) if q else []
        # 问题驱动预检索：用 fact 原文作 query，每轮对所有 fact 预取（写回续命）
        elif self.recall_mode == "passive_q":
            sh, seen = [], set()
            for f in self.facts:
                for h in self.eng.retrieve(f["text"], budget=self.retr):
                    if h not in seen:
                        seen.add(h)
                        sh.append(h)
            self._passive_shadows = sh
        # 恢复模式：闲置期同 off；待 recall_active 置位后同 passive_q（写回续命）
        elif self.recall_mode == "recover" and self.recall_active:
            sh, seen = [], set()
            for f in self.facts:
                for h in self.eng.retrieve(f["text"], budget=self.retr):
                    if h not in seen:
                        seen.add(h)
                        sh.append(h)
            self._passive_shadows = sh

    def _resolve(self, em_ids):
        res = {}
        for f in self.facts:
            fid = self._fid[f["id"]]
            if fid in em_ids:
                res[f["id"]] = "atom"
                continue
            cur = self.eng.atoms[fid].owner
            depth, found = 0, None
            while cur is not None:
                if cur in em_ids:
                    found = f"node{depth}"
                    break
                cur = self.eng.nodes[cur].parent
                depth += 1
            res[f["id"]] = found or "gone"
        return res

    def probe(self):
        em = self.eng.assemble(None, budget=self.budget, allow_merge=False)
        base_txt = self.eng.render_emission(em)
        kept_base = {f["id"]: _contains(base_txt, f) for f in self.facts}
        resolution = self._resolve({it for _, it, _ in em})
        res = {
            "kept": sum(kept_base.values()),
            "len": len(base_txt),
            "resolution": {
                "atom": sum(1 for v in resolution.values() if v == "atom"),
                "node": sum(1 for v in resolution.values() if v.startswith("node")),
                "gone": sum(1 for v in resolution.values() if v == "gone"),
            },
        }
        if self.recall_mode == "passive":
            em_p = self.eng.assemble(self._passive_shadows, budget=self.budget,
                                     allow_merge=False)
            p_txt = self.eng.render_emission(em_p)
            res["passive_kept"] = sum(1 for f in self.facts if _contains(p_txt, f))
        elif self.recall_mode in ("passive_q", "recover"):
            em_p = self.eng.assemble(self._passive_shadows, budget=self.budget,
                                     allow_merge=False)
            p_txt = self.eng.render_emission(em_p)
            res["passiveq_kept"] = sum(1 for f in self.facts if _contains(p_txt, f))
        if self.recall_mode == "active":
            rescued = 0
            for f in self.facts:
                if kept_base[f["id"]]:
                    continue
                hits = self.eng.retrieve(f["text"], budget=self.retr)
                ht = "\n".join(_hit_text(self.eng, h) for h in hits)
                if _contains(ht, f):
                    rescued += 1
            res["rescued"] = rescued
            res["active_kept"] = sum(kept_base.values()) + rescued
        return res


# ── rolling 臂 ──────────────────────────────────────────

class RollingArm:
    """极小滚动摘要：summary（累积摘要）+ 最近 keep 条原文；超预算时把最老的原文折进摘要。
    长期记忆 = 无界 flat RAG 索引（不压缩），用于被动/主动召回探针。"""
    def __init__(self, llm, facts, rag=None, budget=16000, keep=40, summary_cap=200):
        self.llm = llm
        self.facts = facts
        self.rag = rag
        self.budget = budget
        self.keep = keep
        self.summary_cap = summary_cap
        self.summary = ""
        self.msgs = []
        self.last_batch = []

    def _summarize(self, prev, middle):
        content = (prev + "\n" + "\n".join(middle))[:4000]
        prompt = ("把以下内容概括成一行（≤%d字），必须保留专有名词、数字、代码标识、关键事实，"
                  "丢弃寒暄与冗余；只输出摘要本身：\n" % self.summary_cap) + content
        text, _u, _l = self.llm.chat("你是一个可靠的中文记忆蒸馏器。", prompt)
        return (text or "").strip()[:self.summary_cap]

    def _total(self):
        return len(self.summary) + sum(len(m) for m in self.msgs)

    def append(self, msg):
        self.msgs.append(msg)
        if self.rag is not None:
            self.rag.add(msg)
        self._compress()

    def _compress(self):
        while self._total() > self.budget and len(self.msgs) > self.keep:
            middle = self.msgs[:-self.keep]
            self.msgs = self.msgs[-self.keep:]
            self.summary = self._summarize(self.summary, middle)
        while self._total() > self.budget and len(self.msgs) > 1:
            self.msgs.pop(0)

    def context(self):
        return (self.summary + "\n" if self.summary else "") + "\n".join(self.msgs)

    def probe(self):
        ctx = self.context()
        base = {f["id"]: _contains(ctx, f) for f in self.facts}
        res = {"kept": sum(base.values())}
        if self.rag is None:
            return res
        q = "\n".join(self.last_batch).strip()
        p_txt = ctx
        if q:
            p_txt = ctx + "\n" + "\n".join(t for _, t in self.rag.retrieve(q))
        res["passive_kept"] = sum(1 for f in self.facts if _contains(p_txt, f))
        # 问题驱动预检索：用每条 fact 原文 query，取回片段拼进上下文
        pq_txt = ctx + "\n" + "\n".join(
            t for f in self.facts for _, t in self.rag.retrieve(f["text"]))
        res["passiveq_kept"] = sum(1 for f in self.facts if _contains(pq_txt, f))
        rescued = 0
        for f in self.facts:
            if base[f["id"]]:
                continue
            hits = self.rag.retrieve(f["text"])
            if _contains("\n".join(t for _, t in hits), f):
                rescued += 1
        res["rescued"] = rescued
        res["active_kept"] = sum(base.values()) + rescued
        return res


# ── 主流程 ──────────────────────────────────────────────

def _parse_modes(s):
    if s == "all":
        return ["off", "passive", "active", "passive_q"]
    return [s]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--facts", default=os.path.join(_HERE, "facts.json"))
    ap.add_argument("--distractors", default=os.path.join(_REALTEST, "editbench", "ahi_script.txt"))
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--per-round", type=int, default=8, help="每轮读入的 distractor 句数")
    ap.add_argument("--check-every", type=int, default=4)
    ap.add_argument("--mock", action="store_true", help="零 LLM 蒸馏替身（快速看形状）")
    ap.add_argument("--n-facts", type=int, default=0, help=">0 时合成到 N 条事实（压满三档曲线）")
    ap.add_argument("--summary-cap", type=int, default=200, help="rolling 摘要 cap（字符；对齐 tree 单行粒度）")
    ap.add_argument("--recall-mode", choices=["off", "passive", "active", "passive_q", "recover", "all"], default="all",
                    help="off=零救援 / passive=随输入预检索 / active=定向救援 / passive_q=问题驱动预检索 / "
                         "recover=两阶段（先闲置压到阈值再开召回）/ all=四臂同跑")
    ap.add_argument("--recover-below", type=float, default=0.0,
                    help="recover 模式：off 老化到保留率 ≤ 此值（如 0.18）后开启召回")
    ap.add_argument("--retr-budget", type=int, default=6, help="tree 检索 top-k（对齐 flat RAG top_k）")
    ap.add_argument("--rag-chunk", type=int, default=400, help="flat RAG 分块字符数（对齐真实 flat-rag）")
    ap.add_argument("--rag-top-k", type=int, default=6, help="flat RAG 检索 top-k")
    ap.add_argument("--rag-min-score", type=float, default=0.2, help="flat RAG 向量最低分")
    ap.add_argument("--no-rolling", action="store_true",
                    help="跳过 rolling 臂（并行三实例时，仅 baseline 实例带 rolling）")
    ap.add_argument("--merge", action="store_true",
                    help="离线合并 out 目录下的 memcurve_{off,passive,active}.json 并出图（零 LLM）")
    ap.add_argument("--plot", action="store_true", help="生成 matplotlib 曲线图")
    ap.add_argument("--out", default=os.path.join(_HERE, "out"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if args.merge:
        _merge_modes(args.out)
        return
    cfg = json.load(open(args.config, encoding="utf-8"))
    facts = load_facts(args.facts)
    if args.n_facts and args.n_facts > len(facts):
        facts = synth_facts(args.n_facts, facts)
    distractors = load_distractors(args.distractors)
    llm = make_llm(cfg, args.mock)
    modes = _parse_modes(args.recall_mode)
    if args.recover_below > 0:
        modes = ["recover"]

    # 碰撞自检：fact token 不得出现在 distractor 流里
    dtext = "\n".join(distractors)
    coll = [(f["id"], k) for f in facts for k in f["keys"] if k in dtext]
    if coll:
        print("[warn] fact token 与 distractor 碰撞:", coll)
    print(f"[memcurve] recall_modes={modes} facts={len(facts)} "
          f"distractors={len(distractors)} mock={args.mock}")

    trees = {m: TreeArm(cfg, llm, facts, budget=args.budget, retr=args.retr_budget,
                        recall_mode=m) for m in modes}
    rolling = None
    if not args.no_rolling:
        rag = FlatRAGIndex(args.rag_chunk, args.rag_top_k, args.rag_min_score)
        rolling = RollingArm(llm, facts, rag=rag, budget=args.budget, summary_cap=args.summary_cap)
        for f in facts:
            rolling.append(f["text"])

    results = {"facts": [f["id"] for f in facts], "budget": args.budget,
               "rounds": args.rounds, "per_round": args.per_round, "mock": args.mock,
               "recall_modes": modes, "checkpoints": []}

    def checkpoint(x, note=""):
        rec = {"x_chars": x, "note": note, "tree": {}}
        if rolling is not None:
            rec["rolling"] = rolling.probe()
        for m in modes:
            rec["tree"][m] = trees[m].probe()
        results["checkpoints"].append(rec)
        parts = [f"tree-{m}={_eff_kept(rec['tree'][m], m):3d}" for m in modes]
        if rolling is not None:
            parts += [f"roll={rec['rolling']['kept']:3d}",
                      f"roll+被动={rec['rolling'].get('passive_kept', -1):3d}",
                      f"roll+问预={rec['rolling'].get('passiveq_kept', -1):3d}",
                      f"roll+主动={rec['rolling'].get('active_kept', -1):3d}"]
        print(f"[{note or 'cp':>4}] x={x:6d}  " + "  ".join(parts), flush=True)

    recover_done = False

    def maybe_recover(x):
        nonlocal recover_done
        if args.recover_below > 0 and not recover_done and trees.get("recover") is not None:
            kept = results["checkpoints"][-1]["tree"]["recover"]["kept"]
            if kept / max(1, len(facts)) <= args.recover_below:
                trees["recover"].recall_active = True
                recover_done = True
                results["recover_at_x"] = x
                print(f"[recover] 保留率 {kept}/{len(facts)}={kept/len(facts):.2f} ≤ {args.recover_below:.2f}"
                      f" → 开启召回（passive_q 写回续命）@ x={x}", flush=True)
        return recover_done

    checkpoint(0, "t0")
    maybe_recover(0)

    x = 0
    it = iter(distractors)
    for r in range(args.rounds):
        batch = []
        for _ in range(args.per_round):
            s = next(it, None)
            if s is None:
                break
            batch.append(s)
            x += len(s)
        if not batch:
            break
        for m in modes:
            trees[m].age(batch)
        if rolling is not None:
            for s in batch:
                rolling.append(s)
            rolling.last_batch = list(batch)
        if (r + 1) % args.check_every == 0:
            checkpoint(x, f"r{r+1}")
            maybe_recover(x)

    mode_tag = "all" if len(modes) > 1 else modes[0]
    out_json = os.path.join(args.out, f"memcurve_{mode_tag}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n[done] → {out_json}")
    if args.plot:
        _plot(results, os.path.join(args.out, f"memcurve_{mode_tag}.png"))


def _merge_modes(out):
    """合并并行三实例产物（memcurve_{off,passive,active}.json）→ memcurve_all.json + png。"""
    modes = ["off", "passive", "active", "passive_q"]
    data = {}
    for m in modes:
        p = os.path.join(out, f"memcurve_{m}.json")
        if os.path.exists(p):
            data[m] = json.load(open(p, encoding="utf-8"))
    present = [m for m in modes if m in data]
    if not present:
        print(f"[merge] {out} 下无 memcurve_{{off,passive,active}}.json")
        return
    n = min(len(data[m]["checkpoints"]) for m in present)
    cks = []
    for i in range(n):
        c0 = data[present[0]]["checkpoints"][i]
        rec = {"x_chars": c0["x_chars"], "note": c0["note"], "tree": {}}
        for m in present:
            rec["tree"][m] = data[m]["checkpoints"][i]["tree"][m]
        for m in present:
            if "rolling" in data[m]["checkpoints"][i]:
                rec["rolling"] = data[m]["checkpoints"][i]["rolling"]
                break
        cks.append(rec)
    base = data[present[0]]
    merged = {"facts": base["facts"], "budget": base["budget"], "rounds": base["rounds"],
              "per_round": base["per_round"], "mock": base.get("mock", False),
              "recall_modes": present, "checkpoints": cks}
    jp = os.path.join(out, "memcurve_all.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"[merge] → {jp} (modes={present})")
    _plot(merged, os.path.join(out, "memcurve_all.png"))


def _plot(results, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB",
                                            "Arial Unicode MS", "STHeiti", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print("[warn] matplotlib 不可用，跳过绘图:", e)
        return
    cps = results["checkpoints"]
    x = [c["x_chars"] for c in cps]
    n = len(results["facts"])
    modes = results.get("recall_modes", ["off"])
    tcolor = {"off": "#d62728", "passive": "#ff7f0e", "active": "#7f0000",
              "passive_q": "#2ca02c", "recover": "#6a51a3"}
    rcolor = {"kept": "#1f77b4", "passive_kept": "#17becf", "active_kept": "#08306b",
              "passiveq_kept": "#2ca02c"}

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(8.5, 11), sharex=True)

    # 上：tree 遗忘曲线（off / passive / active）
    for m in modes:
        y = [_eff_kept(c["tree"][m], m) / n for c in cps]
        ax1.plot(x, y, "o-", color=tcolor.get(m, None), label=f"tree-{m}")
    ax1.set_ylabel("事实保留率 R(t)")
    ax1.set_title("tree_v2：事实保留率/可达率（recover = 先闲置压缩至阈值、再开召回）")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(-0.05, 1.05)
    if "recover_at_x" in results:
        ax1.axvline(results["recover_at_x"], color="#6a51a3", ls=":", lw=1.2)
        ax1.text(results["recover_at_x"], 1.02, " 开召回", color="#6a51a3", fontsize=8, va="top")

    # 中：rolling(+flat RAG) 遗忘曲线
    for key, lab in (("kept", "rolling 上下文"),
                     ("passive_kept", "rolling+被动预检索(输入)"),
                     ("passiveq_kept", "rolling+问题驱动预检索"),
                     ("active_kept", "rolling+主动召回")):
        if key not in cps[0].get("rolling", {}):
            continue
        y = [c["rolling"].get(key, 0) / n for c in cps]
        ax2.plot(x, y, "s--", color=rcolor[key], label=lab)
    ax2.set_ylabel("事实保留率 R(t)")
    ax2.set_title("rolling + 无界 flat RAG")
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(-0.05, 1.05)

    # 下：tree 分辨率瀑布（原子→节点→消失），用 off 基线臂
    base_mode = "off" if "off" in modes else modes[0]
    atom = [c["tree"][base_mode]["resolution"]["atom"] / n for c in cps]
    node = [c["tree"][base_mode]["resolution"]["node"] / n for c in cps]
    gone = [c["tree"][base_mode]["resolution"]["gone"] / n for c in cps]
    ax3.stackplot(x, atom, node, gone,
                  labels=["原子（全分辨率）", "摘要节点（要点）", "消失"],
                  colors=["#2ca02c", "#ff7f0e", "#7f7f7f"], alpha=0.8)
    ax3.set_xlabel("累计处理字符数")
    ax3.set_ylabel(f"tree-{base_mode} 分辨率占比")
    ax3.legend(loc="upper right")
    ax3.set_ylim(0, 1.0)
    ax3.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path)
    print(f"[plot] → {path}")


if __name__ == "__main__":
    main()
