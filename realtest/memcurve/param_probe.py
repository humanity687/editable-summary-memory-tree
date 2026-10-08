#!/usr/bin/env python3
"""param_probe.py — tick()/merge() 结构参数探针（零 LLM，结构演化与 LLM 无关）。

结构演化（HP/衰减/merge 时机/树高/扇出）**不依赖蒸馏文本**，只由 tick/merge 参数决定，
故用 mock 即可精确复现真实结构。本脚本：
- 逐轮记录窗外原子/节点数、merge_signal 判定、实际 merge 数与扇出；
- 输出终态结构（树高、存活节点、铺陈长度、总 merge、force/regular 占比、平均扇出）；
- 支持 --set k=v 覆盖参数做敏感性扫描。

用法：
  python3 realtest/memcurve/param_probe.py --rounds 80 --n-facts 100
  python3 realtest/memcurve/param_probe.py --rounds 80 --set merge_fanout_cap=6
  python3 realtest/memcurve/param_probe.py --sweep merge_fanout_cap=6,12,24
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
for p in (_REALTEST, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from memcurve import (TreeArm, load_facts, synth_facts, load_distractors,  # noqa: E402
                      make_llm)


def out_counts(eng):
    ts = set(eng.tiling)
    oa = sum(eng.atoms[aid].weight for aid in ts
             if aid in eng.atoms and eng.atoms[aid].alive
             and eng.atoms[aid].hp < eng.attention_out)
    on = sum(1 for nid in ts
             if nid in eng.nodes and eng.nodes[nid].alive
             and not eng._transparent_ph(eng.nodes[nid])
             and eng.nodes[nid].hp < eng.attention_out)
    return oa, on


def run(cfg, facts, distractors, rounds, per_round, n_facts):
    t = TreeArm(cfg, make_llm(cfg, True), facts, budget=16000, retr=6, recall_mode="off")
    eng = t.eng
    sig_cnt = {"force": 0, "regular": 0, "none": 0}
    rows = []
    x = 0
    it = iter(distractors)
    for r in range(rounds):
        batch = []
        for _ in range(per_round):
            s = next(it, None)
            if s is None:
                break
            batch.append(s)
            x += len(s)
        if not batch:
            break
        for s in batch:
            eng.insert_units([(s, {"source": "distractor"})], left_id=eng.tail)
        eng.tick(1)
        oa, on = out_counts(eng)
        sig = eng.merge_signal()
        sig_cnt[sig or "none"] += 1
        n0 = len(eng.events)
        if sig == "force":
            eng.merge_pass(force_out=True)
        elif sig == "regular":
            eng.merge_pass()
        rows.append({"r": r + 1, "x": x, "out_atoms": oa, "out_nodes": on,
                     "sig": sig or "none", "merges": len(eng.events) - n0})
    heights = eng._heights()
    merges = [e for e in eng.events if e[0] == "merge"]
    fanouts = [e[2] for e in merges]
    alive_nodes = sum(1 for nd in eng.nodes.values() if nd.alive)
    node_depth = {}
    for nid, nd in eng.nodes.items():
        if nd.alive:
            d = eng._depth(nid)
            node_depth[d] = node_depth.get(d, 0) + 1
    return {
        "height": max(heights.values()) if heights else 0,
        "alive_nodes": alive_nodes,
        "tiling": len(eng.tiling),
        "total_merges": len(merges),
        "force": sig_cnt["force"], "regular": sig_cnt["regular"], "none": sig_cnt["none"],
        "fanout_max": max(fanouts) if fanouts else 0,
        "fanout_avg": round(sum(fanouts) / len(fanouts), 2) if fanouts else 0,
        "merges_per_r10": round(sum(m["merges"] for m in rows) / max(len(rows), 1) * 10, 2),
        "node_depth_hist": node_depth,
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--facts", default=os.path.join(_HERE, "facts.json"))
    ap.add_argument("--distractors", default=os.path.join(_REALTEST, "editbench", "ahi_script.txt"))
    ap.add_argument("--rounds", type=int, default=80)
    ap.add_argument("--per-round", type=int, default=8)
    ap.add_argument("--n-facts", type=int, default=100)
    ap.add_argument("--set", action="append", default=[], help="参数覆盖 k=v（可多次）")
    ap.add_argument("--sweep", default="", help="扫一个参数：k=v1,v2,...")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    base_cfg = json.load(open(args.config, encoding="utf-8"))
    facts = load_facts(args.facts)
    if args.n_facts and args.n_facts > len(facts):
        facts = synth_facts(args.n_facts, facts)
    distractors = load_distractors(args.distractors)

    def cast(v):
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v

    configs = {}
    if args.sweep:
        k, vals = args.sweep.split("=", 1)
        for v in vals.split(","):
            configs[f"{k}={v}"] = {k: cast(v)}
    elif args.set:
        over = {}
        for kv in args.set:
            k, v = kv.split("=", 1)
            over[k] = cast(v)
        configs["override"] = over
    else:
        configs["baseline"] = {}

    all_out = {}
    for name, over in configs.items():
        cfg = dict(base_cfg)
        cfg.update(over)
        res = run(cfg, facts, distractors, args.rounds, args.per_round, args.n_facts)
        all_out[name] = res
        rows = res.pop("rows")
        print(f"\n=== {name} === over={over}")
        print(f"  height={res['height']} alive_nodes={res['alive_nodes']} tiling={res['tiling']} "
              f"merges={res['total_merges']} (force={res['force']} regular={res['regular']} none={res['none']}) "
              f"fanout avg/max={res['fanout_avg']}/{res['fanout_max']} merges/10r={res['merges_per_r10']}")
        # 每 10 轮的窗外压力
        for i in range(0, len(rows), 10):
            seg = rows[i:i + 10]
            print(f"   r{seg[0]['r']:>2}-{seg[-1]['r']:>2}: out_atoms~{seg[-1]['out_atoms']:>3} "
                  f"out_nodes~{seg[-1]['out_nodes']:>2} sig={ {k: sum(1 for s in seg if s['sig']==k) for k in ('force','regular','none')} } "
                  f"merges={sum(s['merges'] for s in seg)}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "param_probe.json"), "w", encoding="utf-8") as f:
            json.dump(all_out, f, ensure_ascii=False, indent=1)
        print(f"\n[done] → {args.out}/param_probe.json")


if __name__ == "__main__":
    main()
