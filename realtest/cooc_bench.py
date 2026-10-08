#!/usr/bin/env python3
"""cooc_bench.py — 概括节点 Demo：四层题型 + 检索对检索（pre/post）on/off 对照。

核心实验（用户加测）：**先 Q/A → 概括创建 → 再 Q/A**，看概括中介是否产生实质增量。
- off = RealEngine（v2 逐字节等价，增量分母）
- on  = ConceptEngine（RealEngine + 共现中介层）

压力旋钮：--extra-seed（第二篇文章当干扰）、--assembly-budget（减小上下文预算）。
四层题（§4）：a 直接可达 / b 共现中介 / c 编辑跨接 / d 脏区旁路。
"""
import argparse
import json
import os
import random
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from llmclient import make_client
from realengine import RealEngine, engine_kwargs
from textutil import norm_cjk as norm
from cooc import ConceptEngine
from splitter import split_sentences

SYSTEM = """你是长程记忆基准测试的应答 Agent。你的全部记忆来自【记忆流】（每轮全新组装，不依赖对话历史）。
【记忆流格式】N12·摘要 为摘要卡；#34·原文 为原文句卡；⊂Nx 表示其父卡；缩进表示层级；
"召回影子"为检索注入的旧内容；"占位"表示该区域近期被修改、摘要暂缺。
【纪律】答案必须依据记忆流内容作答；记忆流中看不到的信息回答"记忆流中无此信息"，禁止编造。
提问轮只输出 1~2 句事实答案，不解释。"""


def content_of(eng, item):
    if eng._is_atom(item):
        return eng.atoms[item].value or ""
    return eng.nodes[item].value or ""


def story_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_HERE, "config.json"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "seed_cooc", "questions.json"))
    ap.add_argument("--seed-dir", default=os.path.join(_HERE, "seed2"))
    ap.add_argument("--extra-seed", default=None, help="第二篇文章目录（加大干扰压力）")
    ap.add_argument("--cooc", choices=["on", "off"], default="on")
    ap.add_argument("--out", default=None)
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--warmup-passes", type=int, default=6)
    ap.add_argument("--warmup-budget", type=int, default=10, help="预热 recall 的条目预算")
    ap.add_argument("--assembly-budget", type=int, default=None, help="覆盖组装字符预算")
    ap.add_argument("--edits", type=int, default=12)
    ap.add_argument("--temp", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--N", type=int, default=None)
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--j", type=int, default=None)
    ap.add_argument("--theta", type=float, default=None)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg.setdefault("ollama", {})["temperature"] = args.temp
    cfg.setdefault("deepseek", {})["temperature"] = args.temp
    llm = make_client(cfg)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    budget = args.assembly_budget or int(cfg.get("assembly_budget", 16000))
    retr_budget = int(cfg.get("retrieval_budget", 6))
    qspec = json.load(open(args.questions, encoding="utf-8"))

    outdir = args.out or os.path.join(_HERE, "out",
                                      f"cooc_{args.cooc}_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)

    kw = engine_kwargs(cfg, budget, retr_budget)
    if args.cooc == "on":
        cooc_params = dict(cfg.get("cooc", {}))
        for key, val in (("N", args.N), ("k", args.k), ("j", args.j), ("theta_coo", args.theta)):
            if val is not None:
                cooc_params[key] = val
        eng = ConceptEngine(llm, cooc_out=os.path.join(outdir, "cooc"),
                            cooc_params=cooc_params, **kw)
    else:
        eng = RealEngine(llm, **kw)

    # ── Phase 0：灌入（可两篇 → 干扰压力）──
    texts = [open(os.path.join(args.seed_dir, "story.txt"), encoding="utf-8").read().strip()]
    if args.extra_seed:
        ep = os.path.join(args.extra_seed, "story.txt")
        if os.path.exists(ep):
            texts.append(open(ep, encoding="utf-8").read().strip())
    paras = [p for t in texts for p in story_paragraphs(t)]
    all_atoms = []
    for i, p in enumerate(paras):
        ids = eng.write(split_sentences(p))
        all_atoms.extend(ids)
        if i % 2 == 1:
            eng.tick(15)
            eng.merge_pass()
    print(f"[Phase 0] {len(texts)} 篇 / {len(paras)} 段 / {len(all_atoms)} 原子, "
          f"节点 {eng.stat()['nodes']['alive']}, 高 {max(eng._heights().values(), default=0)}, "
          f"预算 {budget}", flush=True)

    rng = random.Random(args.seed)
    t0 = time.time()
    results = {"a_pre": [], "b_pre": [], "a_post": [], "b_post": [], "c": [], "d": []}

    def retrieve(q, b=None):
        hits = eng.retrieve(q, budget=b if b is not None else retr_budget)
        d = getattr(eng, "last_direct", None)
        h = getattr(eng, "last_hub", None)
        return hits, (d if d is not None else hits), (h or [])

    def ask(q, shadows):
        emitted = eng.assemble(shadows or None, budget=budget)
        flow = eng.render_emission(emitted)
        ctx = f"{flow}\n\n【当前任务】\n问题：{q}\n（已注入相关召回影子；直接简短作答）"
        text, _u, _l = llm.chat(SYSTEM, ctx)
        eng.tick(1)
        return text

    def run_question(item, layer, bucket):
        q, frags = item["q"], item["frags"]
        hits, direct, hub = retrieve(q)
        direct_txt = " ".join(content_of(eng, i) for i in direct)
        hub_txt = " ".join(content_of(eng, i) for i in hub)
        ans = ask(q, hits)
        rec = {"layer": layer, "id": item["id"],
               "direct_hit": any(norm(f) in norm(direct_txt) for f in frags),
               "hub_hit": any(norm(f) in norm(hub_txt) for f in frags),
               "answer_hit": any(norm(f) in norm(ans) for f in frags),
               "shadows": len(hits), "hub": len(hub)}
        results[bucket].append(rec)
        tag = "".join("1" if rec[k] else "0" for k in ("direct_hit", "hub_hit", "answer_hit"))
        print(f"  {bucket} {item['id']}: d/h/a={tag} shadows={len(hits)} hub={len(hub)}", flush=True)
        return rec

    def run_layer(name, bucket):
        print(f"[{bucket}] layer {name}", flush=True)
        for it in qspec["layers"][name]:
            if (time.time() - t0) / 60 >= args.minutes:
                break
            run_question(it, name, bucket)

    # ── Stage 1（pre）：Q/A（用法即边积累）──
    run_layer("a", "a_pre")
    run_layer("b", "b_pre")

    # ── Stage 2（summarize）：通用用法预热 + 概括创建 ──
    # 公正性：usage 只含预注册的通用主题 query，**不含任何按 (b) 答案定制的定向
    # warmup**（否则等于先知道答案再教共现层连接，测出的增量不可信）。
    warmup = qspec.get("warmup_queries", [])
    for _p in range(args.warmup_passes):
        for q in warmup:
            eng.retrieve(q, budget=args.warmup_budget)
        eng.tick(1)
    if args.cooc == "on":
        made = sum(eng.cooc.maybe_create() for _ in range(10))
    else:
        made = 0
    n_concepts = len(getattr(eng, "cooc", None).concepts) if args.cooc == "on" else 0
    print(f"[summarize] 预热 {args.warmup_passes} 轮；本轮新建 {made}，概括节点 {n_concepts}",
          flush=True)

    # ── Stage 3（post）：同一批 Q/A 复测（看检索增量）──
    run_layer("a", "a_post")
    run_layer("b", "b_post")

    # ── 编辑风暴 + (d) 脏区 / (c) 跨接 ──
    names = [l.strip() for l in open(os.path.join(args.seed_dir, "names.txt"),
                                     encoding="utf-8") if l.strip()]
    para_atoms = None  # 用全量原子做删除候选
    d_q = list(qspec["layers"]["d"])
    c_q = list(qspec["layers"]["c"])
    edits_done = 0
    zhao = [aid for aid, a in eng.atoms.items() if a.alive and "赵老拐" in (a.value or "")]
    if zhao and args.edits > 0:
        aid = zhao[0]
        eng.replace([aid], [eng.atoms[aid].value.replace("赵老拐", "赵某", 1)])
        edits_done += 1
    while edits_done < args.edits and (time.time() - t0) / 60 < args.minutes:
        kind = rng.choices(["rename", "delete", "insert"], weights=[0.6, 0.25, 0.15])[0]
        op = None
        if kind == "rename" and names:
            name = rng.choice(names)
            cand = [aid for aid, a in eng.atoms.items() if a.alive and name in (a.value or "")]
            if cand:
                aid = rng.choice(cand)
                eng.replace([aid], [eng.atoms[aid].value.replace(name, name[0] + "某", 1)])
                op = True
        elif kind == "delete":
            alive = [a for a in all_atoms if a in eng.atoms and eng.atoms[a].alive]
            if len(alive) >= 2:
                i = rng.randrange(len(alive) - 1)
                eng.delete(alive[i:i + rng.randint(1, 3)])
                op = True
        else:
            alive = [a for a in all_atoms if a in eng.atoms and eng.atoms[a].alive]
            if alive:
                aid = rng.choice(alive)
                eng.insert(aid, eng.atoms[aid].next,
                           [f"（插入句{edits_done}）{rng.choice(names) if names else '他'}在门前站了片刻。"])
                op = True
        if not op:
            continue
        edits_done += 1
        if d_q:
            run_question(d_q.pop(0), "d", "d")
        if edits_done % 3 == 0:
            eng.tick(6)
            eng.merge_pass()
    eng.tick(20)
    eng.merge_pass()
    print("[layer c] 编辑跨接", flush=True)
    for it in c_q:
        if (time.time() - t0) / 60 >= args.minutes:
            break
        run_question(it, "c", "c")

    # ── 汇总 ──
    def rate(rs, key):
        return round(sum(1 for r in rs if r[key]) / len(rs), 3) if rs else 0.0

    def block(rs):
        return {"n": len(rs), "direct": rate(rs, "direct_hit"),
                "hub": rate(rs, "hub_hit"), "answer": rate(rs, "answer_hit")}

    summary = {
        "mode": args.cooc, "model": getattr(llm, "model", "?"),
        "two_articles": bool(args.extra_seed), "budget": budget,
        "elapsed_s": round(time.time() - t0, 1),
        "pre": {"a": block(results["a_pre"]), "b": block(results["b_pre"])},
        "post": {"a": block(results["a_post"]), "b": block(results["b_post"])},
        "c": block(results["c"]), "d": block(results["d"]),
        "raw": results,
    }
    if args.cooc == "on":
        cooc = eng.cooc
        summary["cooc_stats"] = {
            "edges": len(cooc.edges), "concepts": len(cooc.concepts),
            "concept_titles": [c["title"] for c in cooc.concepts.values()],
            "concept_members": [len(c["members"]) for c in cooc.concepts.values()],
            "hub_calls": sum(1 for r in cooc.recall_log if r["via"] == "hub"),
        }
    with open(os.path.join(outdir, "cooc_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("\n===== 结果 (pre → post) =====")
    for layer in ("a", "b"):
        pre, post = summary["pre"][layer], summary["post"][layer]
        print(f"  {layer}: direct {pre['direct']}→{post['direct']}  "
              f"hub {pre['hub']}→{post['hub']}  answer {pre['answer']}→{post['answer']}")
    print(f"  c/d: answer {summary['c']['answer']} / {summary['d']['answer']}")
    if "cooc_stats" in summary:
        print("  cooc:", json.dumps(summary["cooc_stats"], ensure_ascii=False))
    print(f"\n产出目录: {outdir}")


if __name__ == "__main__":
    main()
