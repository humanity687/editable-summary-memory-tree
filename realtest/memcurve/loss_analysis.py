#!/usr/bin/env python3
"""loss_analysis.py — memcurve 附：定位"事实 token 在树的哪一层被摘要丢掉"。

对 off 臂（零召回）复现 memcurve 的老化序列，每个 checkpoint 对每条 fact：
- 沿 owner→root 的祖先链逐层检查 token 是否还在该节点摘要里；
- 记录 keeps（服务上下文是否保留）、以及丢失发生在第几层（L1=原子→首层摘要，
  L2=摘要节点的摘要，L3+=更高层）；
- 同时统计树高 / 服务节点深度 / merge 事件与触发类型（force/regular）。

用法：
  python3 realtest/memcurve/loss_analysis.py --mock --n-facts 100 --rounds 80 --check-every 4
  python3 realtest/memcurve/loss_analysis.py --n-facts 100 --rounds 80 --check-every 4 \
      --out realtest/memcurve/out/loss
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from memcurve import (TreeArm, load_facts, synth_facts, load_distractors,  # noqa: E402
                      make_llm, _contains)


def _fact_chain(eng, fid):
    chain = []
    nid = eng.atoms[fid].owner
    while nid is not None:
        chain.append(nid)
        nid = eng.nodes[nid].parent
    return chain


def _node_has(eng, nid, fact):
    nd = eng.nodes.get(nid)
    if nd is None or not nd.alive or nd.value is None or nd.placeholder:
        return False
    return _contains(nd.value, fact)


def analyze_checkpoint(t, served_txt, em, events_delta):
    eng = t.eng
    cats = {"kept": 0, "lost@L1": 0, "lost@L2": 0, "lost@L3+": 0,
            "lost@top": 0, "unowned": 0, "atom_dead": 0}
    levels_present = []          # 每条 fact: 祖先链逐层 present 的层号列表
    n_present = [0, 0, 0, 0, 0]  # L1..L5 存在 token 的 fact 数
    served_depths = []
    detail = []
    for f in t.facts:
        fid = t._fid[f["id"]]
        a = eng.atoms.get(fid)
        if a is None or not a.alive:
            cats["atom_dead"] += 1
            detail.append({"id": f["id"], "cat": "atom_dead"})
            continue
        chain = _fact_chain(eng, fid)
        pres = [_node_has(eng, nid, f) for nid in chain]
        kept = _contains(served_txt, f)
        for i, p in enumerate(pres):
            if p:
                n_present[min(i, 4)] += 1
        levels_present.append([i + 1 for i, p in enumerate(pres) if p])
        if kept:
            cats["kept"] += 1
            cat = "kept"
        elif not chain:
            cats["unowned"] += 1
            cat = "unowned"
        elif not pres[0]:
            cats["lost@L1"] += 1
            cat = "lost@L1"
        elif len(pres) == 1:
            cats["lost@top"] += 1
            cat = "lost@top"
        elif not pres[1]:
            cats["lost@L2"] += 1
            cat = "lost@L2"
        elif not all(pres):
            cats["lost@L3+"] += 1
            cat = "lost@L3+"
        else:
            cats["lost@top"] += 1
            cat = "lost@top"
        if cat != "kept":
            detail.append({"id": f["id"], "cat": cat,
                           "chain_depth": len(chain),
                           "pres_levels": [i + 1 for i, p in enumerate(pres) if p]})
    heights = eng._heights()
    top_items = [it for it in eng.tiling if not eng._is_atom(it)]
    for it in top_items:
        served_depths.append(eng._depth(it))
    ev = events_delta
    return {
        "kept": cats["kept"],
        "cats": cats,
        "level_present_counts": {"L1": n_present[0], "L2": n_present[1],
                                 "L3": n_present[2], "L4": n_present[3], "L5": n_present[4]},
        "tree_height": (max(heights.values()) if heights else 0),
        "n_nodes_alive": sum(1 for nd in eng.nodes.values() if nd.alive),
        "n_tiling": len(eng.tiling),
        "served_depth_hist": {str(d): served_depths.count(d) for d in sorted(set(served_depths))},
        "merge_events": sum(1 for e in ev if e[0] == "merge"),
        "rebuild_events": sum(1 for e in ev if e[0] in ("rebuild", "drop")),
        "detail_missing": detail,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--facts", default=os.path.join(_HERE, "facts.json"))
    ap.add_argument("--distractors", default=os.path.join(_REALTEST, "editbench", "ahi_script.txt"))
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--rounds", type=int, default=80)
    ap.add_argument("--per-round", type=int, default=8)
    ap.add_argument("--check-every", type=int, default=4)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--n-facts", type=int, default=100)
    ap.add_argument("--set", action="append", default=[], help="参数覆盖 k=v（可多次）")
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "loss"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    for kv in args.set:
        k, v = kv.split("=", 1)
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
        cfg[k] = v
    facts = load_facts(args.facts)
    if args.n_facts and args.n_facts > len(facts):
        facts = synth_facts(args.n_facts, facts)
    distractors = load_distractors(args.distractors)
    llm = make_llm(cfg, args.mock)
    t = TreeArm(cfg, llm, facts, budget=args.budget, retr=6, recall_mode="off")

    rows = []

    def snap(x, note):
        em = t.eng.assemble(None, budget=args.budget, allow_merge=False)
        served = t.eng.render_emission(em)
        delta = t.eng.events[ev_mark[0]:]
        ev_mark[0] = len(t.eng.events)
        row = {"x": x, "note": note}
        row.update(analyze_checkpoint(t, served, em, delta))
        rows.append(row)
        c = row["cats"]
        print(f"[{note:>4}] x={x:6d} kept={row['kept']:3d} | "
              f"L1present={row['level_present_counts']['L1']:3d} "
              f"L2={row['level_present_counts']['L2']:3d} "
              f"L3={row['level_present_counts']['L3']:3d} | "
              f"lost L1={c['lost@L1']} L2={c['lost@L2']} L3+={c['lost@L3+']} "
              f"top={c['lost@top']} dead={c['atom_dead']} | "
              f"h={row['tree_height']} nodes={row['n_nodes_alive']} tiling={row['n_tiling']} "
              f"merges={row['merge_events']}", flush=True)

    ev_mark = [0]
    snap(0, "t0")
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
        t.age(batch)
        if (r + 1) % args.check_every == 0:
            snap(x, f"r{r+1}")

    with open(os.path.join(args.out, "loss_analysis.json"), "w", encoding="utf-8") as f:
        # 去掉逐条 detail 之外的巨型字段，保留完整
        json.dump({"facts": len(facts), "mock": args.mock, "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n[done] → {args.out}/loss_analysis.json")


if __name__ == "__main__":
    main()
