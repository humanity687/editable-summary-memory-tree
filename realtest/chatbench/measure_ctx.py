#!/usr/bin/env python3
"""measure_ctx.py — 测量四臂每题的上下文使用长度（不调作答 LLM；仅本地检索/组装）。

输出：每臂 T2/T3 的初始上下文字符数/估计 token 数（均值/最小/最大）、每次 recall 增量、
以及树的组装预算上限与实际用量。
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _REALTEST)
sys.path.insert(0, _HERE)

import compare as C          # noqa: E402
from tree_io import load_tree  # noqa: E402
from llmclient import estimate_tokens  # noqa: E402


def stats(v):
    if not v:
        return (0, 0, 0, 0.0)
    return (len(v), min(v), max(v), sum(v) / len(v))


def main():
    cfg = json.load(open(os.path.join(_REALTEST, "config.json"), encoding="utf-8"))
    cfg["provider"] = "deepseek"
    cfg.setdefault("deepseek", {})["temperature"] = 0.0
    llm = C.make_client(cfg)
    msgs = C.load_msgs(os.path.join(_HERE, "corpus", "corpus.jsonl"))
    mmap = C.msg_map(msgs)
    qspec = json.load(open(os.path.join(_HERE, "questions_ctx.json"), encoding="utf-8"))
    tree_path = os.path.join(_HERE, "out", "tree_ctx.json.gz")
    budget = 16000
    retr = 8

    # ── FullSend ──
    full = "\n".join(f"[msg:{m['msg_id'][:8]}] {m['content']}" for m in msgs)
    report = {"fullsend": {"ctx": len(full), "tokens": estimate_tokens(full), "note": "常量/题"}}

    # ── RAPTOR（缓存）──
    enc = C._load_encoder()
    rap = C.Raptor(msgs, enc, llm, cache=os.path.join(_HERE, "out", "raptor_ctx.json.gz"))
    r_sizes, r_toks = [], []
    for it in qspec["t2"] + qspec["t3"]:
        t = rap.retrieve(it["q"])
        r_sizes.append(len(t)); r_toks.append(estimate_tokens(t))
    r2 = rap.retrieve("把上线第一轮各自做了什么总结一下")
    report["raptor"] = {"ctx": stats(r_sizes), "tokens_mean": int(sum(r_toks) / len(r_toks)),
                        "per_recall_block": len(r2), "topk": 12,
                        "note": "collapsed-tree top-12；无字符硬上限"}

    # ── Tree（off / on）──
    kw = C.engine_kwargs(cfg, budget, retr)
    for arm in ("tree_off", "tree_on"):
        eng = (C.ConceptEngine(llm, cooc_out=None, cooc_params=dict(cfg.get("cooc", {})), **kw)
               if arm == "tree_on" else C.RealEngine(llm, **kw))
        load_tree(eng, tree_path)
        C.attach_msg_ids(eng, msgs)
        shadows = []

        def gather(q):
            hits = eng.retrieve(q, budget=retr)
            for x in hits:
                if x not in shadows:
                    shadows.append(x)
            if hasattr(eng, "cooc"):
                eng.cooc.maybe_create()

        for _p in range(4):
            for uq in qspec.get("usage_queries", []):
                gather(uq)
            eng.tick(1)
            eng.merge_pass()
        sizes, toks, emitted_chars = [], [], []
        for it in qspec["t2"] + qspec["t3"]:
            gather(it["q"])
            emitted = C.assemble_for_prompt(eng, shadows, budget)
            flow = C.render_tree_flow(eng, emitted)
            sizes.append(len(flow))
            toks.append(estimate_tokens(flow))
            emitted_chars.append(eng._emission_chars(emitted))
        # 一次 recall 增量（再检索一个 usage 查询，看追加的渲染增量）
        before = len(C.render_tree_flow(eng, C.assemble_for_prompt(eng, shadows, budget)))
        gather("把对账与销账的经过再说一遍")
        after = len(C.render_tree_flow(eng, C.assemble_for_prompt(eng, shadows, budget)))
        info = {"ctx": stats(sizes), "tokens_mean": int(sum(toks) / len(toks)),
                "budget_chars": budget, "emission_mean": int(sum(emitted_chars) / len(emitted_chars)),
                "per_recall_delta": after - before,
                "note": "assemble 硬预算 16000 字符；原子不裁"}
        if hasattr(eng, "cooc"):
            info["concepts"] = len(eng.cooc.concepts)
            info["hub_calls"] = sum(1 for r in eng.cooc.recall_log if r["via"] == "hub")
        report[arm] = info

    out = os.path.join(_HERE, "out", "ctx_usage.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n================ 上下文使用长度 ================")
    print(f"{'arm':10s} {'题数':>4s} {'均值字符':>8s} {'最小':>7s} {'最大':>7s} {'均值token':>9s} {'每次recall增量':>12s}")
    for arm in ("tree_off", "tree_on", "raptor", "fullsend"):
        if arm not in report:
            continue
        r = report[arm]
        if arm == "fullsend":
            print(f"{arm:10s} {len(qspec['t2'])+len(qspec['t3']):>4d} {r['ctx']:>8d} {r['ctx']:>7d} {r['ctx']:>7d} {r['tokens']:>9d} {'—':>12s}")
            continue
        n, mn, mx, mean = r["ctx"]
        print(f"{arm:10s} {n:>4d} {int(mean):>8d} {mn:>7d} {mx:>7d} {r['tokens_mean']:>9d} {r.get('per_recall_delta','—'):>12}")
    print(f"\n→ {out}")
    if "tree_on" in report:
        r = report["tree_on"]
        print(f"tree_on: 概括节点 {r.get('concepts')}，hub 带动 {r.get('hub_calls')}")
    print(f"raptor: 每次 recall 追加块 {report['raptor']['per_recall_block']} 字，最多 10 次")


if __name__ == "__main__":
    main()
