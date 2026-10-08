#!/usr/bin/env python3
"""compare.py — 四臂对比：Tree-off / Tree-on / RAPTOR / FullSend。

统一协议：
- 引用 = (依据: msg:<id8> "原文摘录")；确定性校验 msg_id 存在且摘录为该消息子串。
- 同题库（questions_ctx.json）、同作答 prompt、temp 0、deepseek-flash（1M 上下文）。
- 四臂产物结构一致（replay_result.json），供 score_compare.py 统一评分。

用法：
  python3 compare.py --arm tree_off --save-tree out/tree_msg.json.gz --out out/cmp_tree_off
  python3 compare.py --arm tree_on  --load-tree out/tree_msg.json.gz --out out/cmp_tree_on
  python3 compare.py --arm raptor   --out out/cmp_raptor
  python3 compare.py --arm fullsend --out out/cmp_fullsend
"""
import argparse
import gzip
import json
import math
import os
import re
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _REALTEST)

from llmclient import make_client                      # noqa: E402
from realengine import RealEngine, engine_kwargs      # noqa: E402
from textutil import norm_cjk as _norm                # noqa: E402
from cooc import ConceptEngine                        # noqa: E402
from splitter import split_sentences                  # noqa: E402
from protocol import parse_response                   # noqa: E402
from tree_io import save_tree, load_tree              # noqa: E402
from realengine import _load_encoder                  # noqa: E402

CITE_RE = re.compile(r"msg:([0-9a-fA-F]{6,12})")
CITE_FULL_RE = re.compile(r'msg:([0-9a-fA-F]{6,12})\s*[“"]([^”"]{2,80})[”"]')
QUOTE_RE = re.compile(r'[“"]([^”"]{4,80})[”"]')
SYSTEM = """你是长程记忆的分析助手。你的全部材料来自给定上下文（每轮全新，不依赖对话历史）。
【引用纪律（强制）】
1. 只依据上下文作答，禁止编造；找不到就明确写"上下文中无此信息"。
2. 每个结论句后必须紧跟一条引用，格式严格为：(依据: msg:<消息id> "原文摘录")
   - 消息 id 必须来自上下文里出现过的 [msg:xxxxxxxx] 标签；
   - "原文摘录"必须是该消息里**逐字出现**的文字（10~30 字），不得改写、不得跨句拼接。
3. 写不出合法引用的结论不要输出。
示例：白指出笔记宣称与实测不符 (依据: msg:1a2b3c4d "宿主其余部分不可见")
4. 简洁、分点。"""


# ── 统一引用工具 ────────────────────────────────────────

def load_msgs(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def msg_map(msgs):
    return {m["msg_id"][:8]: (m["content"] or "") for m in msgs}


def cite_check(answer, mmap):
    """统一确定性核验：msg_id 存在 + 紧跟引号逐字属于该消息 + format-agnostic 引号核验。"""
    txt = answer or ""
    ids = CITE_RE.findall(txt)
    valid = [i for i in ids if i in mmap]
    bad = sorted(set(i for i in ids if i not in mmap))[:20]
    pairs = CITE_FULL_RE.findall(txt)
    quote_in_cited = sum(1 for cid, q in pairs
                         if cid in mmap and _norm(q) in _norm(mmap[cid]))
    quotes = QUOTE_RE.findall(txt)
    quote_any = sum(1 for q in quotes
                    if any(_norm(q) in _norm(v) for v in mmap.values()))
    return {"citations": len(ids), "valid_ids": len(valid), "bad": bad,
            "quoted": len(pairs), "quote_in_cited": quote_in_cited,
            "quotes_total": len(quotes), "quote_any": quote_any,
            "chars": len(txt)}


def cited_texts(answer, mmap):
    out = []
    for cid in sorted(set(CITE_RE.findall(answer or ""))):
        if cid in mmap:
            out.append([cid, "msg", mmap[cid][:400]])
    return out


def ask(llm, context, q, extra="", rule="", body="直接作答，逐条带引用。"):
    prompt = (f"{context}\n\n【当前任务】\n问题：{q}\n{extra}\n{rule}{body}")
    text, _u, _l = llm.chat(SYSTEM, prompt)
    return text


# ── Tree arms ──────────────────────────────────────────

def attach_msg_ids(eng, msgs):
    """复用旧树（无 msg metadata）时，用逐句映射把原子挂回 msg_id。"""
    sent2msg = {}
    for m in msgs:
        mid = m["msg_id"][:8]
        for s in split_sentences(m["content"] or ""):
            sent2msg.setdefault(s.strip(), mid)
    hit = miss = 0
    for a in eng.atoms.values():
        if (a.metadata or {}).get("msg"):
            hit += 1
            continue
        v = (a.value or "").strip()
        mid = sent2msg.get(v)
        if not mid:
            for m in msgs:
                if v and v in (m["content"] or ""):
                    mid = m["msg_id"][:8]
                    break
        if mid:
            a.metadata["msg"] = mid
            hit += 1
        else:
            miss += 1
    print(f"  [tree] msg 映射：命中 {hit}，未命中 {miss}", flush=True)


def atom_msg(eng, aid):
    return (eng.atoms[aid].metadata or {}).get("msg", "")


def render_tree_flow(eng, emitted):
    """树渲染（带 msg id，供 msg 级引用）。"""
    lines = []
    for kind, item, _v in emitted:
        if eng._is_atom(item):
            a = eng.atoms[item]
            mid = (a.metadata or {}).get("msg", "")
            tag = "召回原文" if kind == "shadow" else "原文"
            lines.append(f"[msg:{mid}·{tag}] {a.value}")
        else:
            nd = eng.nodes[item]
            srcs = []
            stack, seen = [item], set()
            while stack and len(srcs) < 6:
                c = stack.pop()
                if c in seen:
                    continue
                seen.add(c)
                if eng._is_atom(c):
                    mid = (eng.atoms[c].metadata or {}).get("msg", "")
                    if mid:
                        srcs.append(mid)
                else:
                    stack.extend(eng.nodes[c].children)
            tag = "召回摘要" if kind == "shadow" else "摘要"
            lines.append(f"[摘要·{tag}·源msg:{','.join(srcs)}] {nd.value or '（占位）'}")
    return "\n".join(lines)


def assemble_for_prompt(eng, shadows, budget):
    """按**实际交付渲染**再裁一次：引擎 assemble 的预算用的是较轻的 render_emission，
    而真正发给模型的是 render_tree_flow（带 msg/源msg 标签），必须以后者为准。"""
    emitted = eng.assemble(shadows or None, budget=budget)
    out = list(emitted)
    while len(render_tree_flow(eng, out)) > budget:
        idxs = [i for i, e in enumerate(out) if e[0] in ("node", "shadow")]
        if not idxs:
            break
        i = min(idxs, key=lambda j: eng._item_hp(out[j][1]))
        out.pop(i)
    return out


# ── RAPTOR ─────────────────────────────────────────────

class Raptor:
    def __init__(self, msgs, enc, llm, cache=None, leaf_chars=400):
        self.llm = llm
        self.enc = enc
        self.nodes = []          # {"id","level","text","msgs":[...],"emb":np.ndarray}
        if cache and os.path.exists(cache):
            self._load(cache)
        else:
            self._build(msgs, leaf_chars)
            if cache:
                self._save(cache)

    def _emb(self, texts):
        return self.enc.encode(texts, normalize_embeddings=True, batch_size=32)

    def _build(self, msgs, leaf_chars):
        leaves = []
        for m in msgs:
            txt = (m["content"] or "").strip()
            mid = m["msg_id"][:8]
            for i in range(0, max(1, math.ceil(len(txt) / leaf_chars))):
                seg = txt[i * leaf_chars:(i + 1) * leaf_chars].strip()
                if seg:
                    leaves.append({"level": 0, "text": seg, "msgs": [mid]})
        embs = self._emb([x["text"] for x in leaves])
        for x, e in zip(leaves, embs):
            x["emb"] = e
        self.nodes = leaves
        print(f"  [raptor] leaves={len(leaves)}", flush=True)
        level = 0
        cur = leaves
        while len(cur) > 12 and level < 3:
            level += 1
            groups = self._cluster(cur)
            parents = []
            for g in groups:
                text = self._summarize([cur[i]["text"] for i in g])
                mids = sorted({m for i in g for m in cur[i]["msgs"]})
                parents.append({"level": level, "text": text, "msgs": mids})
            pens = self._emb([p["text"] for p in parents])
            for x, e in zip(parents, pens):
                x["emb"] = e
            print(f"  [raptor] level {level}: {len(parents)} nodes", flush=True)
            self.nodes.extend(parents)
            cur = parents

    def _cluster(self, nodes):
        v = np.stack([n["emb"] for n in nodes])
        n = len(nodes)
        k = max(2, math.ceil(n / 10))
        idx = np.linspace(0, n - 1, k).astype(int)
        cent = v[idx].copy()
        assign = np.zeros(n, dtype=int)
        for _ in range(12):
            d = ((v[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
            assign = d.argmin(1)
            for c in range(k):
                m = assign == c
                if m.any():
                    cent[c] = v[m].mean(0)
        return [list(np.where(assign == c)[0]) for c in range(k)]

    def _summarize(self, parts):
        content = "\n".join(f"- {p[:400]}" for p in parts)[:4000]
        try:
            t, _u, _l = self.llm.chat(
                "你是记忆概括器。", f"把这组对话片段概括成一段中性摘要，保留专有名词、关键事实与因果，只输出摘要：\n{content}")
        except Exception:
            t = content[:300]
        return (t or content[:300]).strip()

    def retrieve(self, q, topk=12):
        qv = self.enc.encode(q, normalize_embeddings=True)
        scored = []
        for n in self.nodes:
            if n.get("emb") is None:
                continue
            scored.append((float(n["emb"] @ qv), n))
        scored.sort(key=lambda x: -x[0])
        picked, lines, seen = [], [], set()
        for s, n in scored:
            key = (n["level"], tuple(n["msgs"][:3]), n["text"][:40])
            if key in seen:
                continue
            seen.add(key)
            picked.append(n)
            head = f"[摘要·源msg:{','.join(n['msgs'][:4])}]" if n["level"] else f"[msg:{n['msgs'][0]}]"
            lines.append(f"{head} {n['text']}")
            if len(picked) >= topk:
                break
        return "\n".join(lines)

    def _save(self, path):
        data = [{"level": n["level"], "text": n["text"], "msgs": n["msgs"]} for n in self.nodes]
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _load(self, path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        embs = self._emb([n["text"] for n in data])
        for n, e in zip(data, embs):
            n["emb"] = e
        self.nodes = data
        print(f"  [raptor] loaded {len(data)} nodes", flush=True)


# ── 作答循环 ────────────────────────────────────────────

def run_questions(llm, mmap, qspec, ctx_fn, recall_fn, max_recall, budget, out_dir,
                  allow_recall=True, reset_fn=None):
    answers = []
    t0 = time.time()

    def answer(q, min_claims=0, extra="", probe=None):
        if reset_fn:                    # 每题清空该题专属的检索累积（防跨题污染）
            reset_fn()
        text = ask(llm, ctx_fn(probe or q), q, extra=extra,
                   rule=(f"材料不足时可用 [recall]关键词[/recall] 再检索（剩余 {{left}} 次）；"
                         if allow_recall else "直接作答，不要使用 recall。"),
                   body=(f"至少给出 {min_claims} 条，逐条带引用。" if min_claims else "直接作答，逐条带引用。"))
        recall_used, left = 0, max_recall
        while allow_recall and left > 0:
            recalls = [b[1] for b in parse_response(text) if b[0] == "recall"]
            if not recalls:
                break
            for rq in recalls[:left]:
                if recall_fn:
                    recall_fn(rq)
                left -= 1
                recall_used += 1
            text = ask(llm, ctx_fn(probe or q), q, extra=extra,
                       rule=f"材料不足时可再用 [recall]（剩余 {left} 次）；",
                       body=(f"至少给出 {min_claims} 条，逐条带引用。" if min_claims else "直接作答，逐条带引用。"))
        return {"q": q, "answer": text, "recall_used": recall_used,
                "cite_check": cite_check(text, mmap), "cited_texts": cited_texts(text, mmap),
                "hub": [], "direct": []}

    for it in qspec.get("t1", []):
        r = answer(it["q"]); r["id"] = it["id"]; r["layer"] = it.get("type", "a")
        r["gold_frags"] = it.get("gold_frags", [])
        r["exact_hit"] = any(_norm(f) in _norm(r["answer"]) for f in r["gold_frags"])
        answers.append(r)
        print(f"  T1 {it['id']}: exact={r['exact_hit']} cite={r['cite_check']}", flush=True)
    for it in qspec.get("t2", []):
        r = answer(it["q"], min_claims=it.get("require_min", 3))
        r["id"] = it["id"]; r["layer"] = "t2"; r["require_min"] = it.get("require_min", 3)
        answers.append(r)
        print(f"  T2 {it['id']}: cite={r['cite_check']}", flush=True)
    for it in qspec.get("t3", []):
        claims = it.get("claims", [])
        numbered = "\n".join(f"{i+1}. {c['claim']}" for i, c in enumerate(claims))
        q = it.get("q", "请逐条核查下列陈述与材料是否一致。")
        extra = (f"\n【待验证陈述】\n{numbered}\n\n对每条输出一行：`编号. 判定`"
                 f"（支持/反驳/无据），附 (依据: msg:<id> \"摘录\")；依据不足写 无据。")
        probe = q + " " + " ".join(c["claim"][:30] for c in claims[:3])
        r = answer(q, extra=extra, probe=probe)
        r["id"] = it["id"]; r["layer"] = "t3"
        r["claims"] = [c["claim"] for c in claims]
        r["labels"] = [c.get("label", "support") for c in claims]
        answers.append(r)
        print(f"  T3 {it['id']}: recall×{r['recall_used']} cite={r['cite_check']}", flush=True)
    return answers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    choices=["tree_off", "tree_on", "raptor", "fullsend"])
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--corpus", default=os.path.join(_HERE, "corpus", "corpus.jsonl"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "questions_ctx.json"))
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--retr-budget", type=int, default=8)
    ap.add_argument("--usage-passes", type=int, default=4)
    ap.add_argument("--max-recall", type=int, default=10)
    ap.add_argument("--save-tree", default=None)
    ap.add_argument("--load-tree", default=None)
    ap.add_argument("--raptor-cache", default=os.path.join(_HERE, "out", "raptor_ctx.json.gz"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    # 四臂统一作答/摘要基座：deepseek-flash（1M 上下文），temperature 0
    cfg["provider"] = "deepseek"
    cfg.setdefault("deepseek", {})["temperature"] = 0.0
    cfg["deepseek"].setdefault("model", "deepseek-v4-flash")
    llm = make_client(cfg)
    msgs = load_msgs(args.corpus)
    mmap = msg_map(msgs)
    qspec = json.load(open(args.questions, encoding="utf-8"))
    print(f"[{args.arm}] {len(msgs)} 条消息 / {sum(len(m['content']) for m in msgs)} 字", flush=True)

    t0 = time.time()
    if args.arm in ("tree_off", "tree_on"):
        kw = engine_kwargs(cfg, args.budget, args.retr_budget)
        eng = (ConceptEngine(llm, cooc_out=os.path.join(args.out, "cooc"),
                             cooc_params=dict(cfg.get("cooc", {})), **kw)
               if args.arm == "tree_on" else RealEngine(llm, **kw))
        if args.load_tree and os.path.exists(args.load_tree):
            load_tree(eng, args.load_tree)
            print(f"  [tree] loaded {len(eng.atoms)} atoms", flush=True)
            attach_msg_ids(eng, msgs)
        else:
            for i, m in enumerate(msgs):
                units = [(s, {"source": m["name"], "msg": m["msg_id"][:8]})
                         for s in split_sentences(m["content"]) if s]
                if units:
                    eng.insert_units(units, left_id=eng.tail)
                if (i + 1) % 40 == 0:
                    eng.tick(6)
                    sig = eng.merge_signal()
                    if sig == "force":
                        eng.merge_pass(force_out=True)
                    elif sig == "regular":
                        eng.merge_pass()
            eng.tick(6); eng.merge_pass()
            if args.save_tree:
                save_tree(eng, args.save_tree)
                print(f"  [tree] saved {args.save_tree}", flush=True)

        shadows: list = []

        def _gather(q):
            hits = eng.retrieve(q, budget=args.retr_budget)
            for x in hits:
                if x not in shadows:
                    shadows.append(x)
            if hasattr(eng, "cooc"):
                eng.cooc.maybe_create()   # 检索即使用：T 阶段也长概括节点

        def ctx_fn(q):
            _gather(q)
            emitted = assemble_for_prompt(eng, shadows, args.budget)
            return render_tree_flow(eng, emitted)

        def recall_fn(q):
            _gather(q)
        # Stage W：usage（两臂同做，保证树演化一致；on 侧长概括节点）
        for _p in range(args.usage_passes):
            for uq in qspec.get("usage_queries", []):
                eng.retrieve(uq, budget=args.retr_budget)
                if hasattr(eng, "cooc"):
                    eng.cooc.maybe_create()
            eng.tick(1); eng.merge_pass()
        print(f"  [tree] W done, atoms={len(eng.atoms)} nodes={eng.stat()['nodes']['alive']} "
              f"cooc={len(eng.cooc.concepts) if hasattr(eng,'cooc') else 0}", flush=True)
        answers = run_questions(llm, mmap, qspec, ctx_fn, recall_fn, args.max_recall,
                                args.budget, args.out, reset_fn=lambda: shadows.clear())
        meta = {"atoms": len(eng.atoms), "nodes": eng.stat()["nodes"]["alive"]}
        if hasattr(eng, "cooc"):
            meta["cooc"] = {"edges": len(eng.cooc.edges), "concepts": len(eng.cooc.concepts),
                            "hub_calls": sum(1 for r in eng.cooc.recall_log if r["via"] == "hub")}

    elif args.arm == "raptor":
        enc = _load_encoder()
        rap = Raptor(msgs, enc, llm, cache=args.raptor_cache)
        extra_ctx = []

        def ctx_fn(q):
            parts = [rap.retrieve(q)] + list(extra_ctx)
            return "\n".join(parts)

        def recall_fn(q):
            extra_ctx.append(rap.retrieve(q))

        answers = run_questions(llm, mmap, qspec, ctx_fn, recall_fn, args.max_recall,
                                args.budget, args.out, reset_fn=lambda: extra_ctx.clear())
        meta = {"nodes": len(rap.nodes)}

    else:  # fullsend
        full = "\n".join(f"[msg:{m['msg_id'][:8]}] {m['content']}" for m in msgs)
        def ctx_fn(q):
            return full
        answers = run_questions(llm, mmap, qspec, ctx_fn, None, 0, args.budget, args.out,
                                allow_recall=False)
        meta = {"chars": len(full)}

    result = {"arm": args.arm, "model": getattr(llm, "model", "?"),
              "n_messages": len(msgs), "elapsed_s": round(time.time() - t0, 1),
              "meta": meta, "answers": answers}
    with open(os.path.join(args.out, "replay_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[{args.arm}] done {result['elapsed_s']}s → {args.out}", flush=True)


if __name__ == "__main__":
    main()
