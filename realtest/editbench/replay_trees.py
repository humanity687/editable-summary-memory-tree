#!/usr/bin/env python3
"""replay_trees.py — 复用已存档的树快照，只重跑作答/复述（不重建/编辑/冷却）。

为什么可复用：树的 build/apply_ledger/settle 不依赖 assembly budget/retrieval budget；
改动预算只影响作答与复述时的**组装/检索**。故加载 gzip 快照 + 新预算重跑即可。

用法：
  python3 realtest/editbench/replay_trees.py \
      --reuse-from realtest/editbench/out/ahi_tree_v3_check/trees \
      --scenario ahi --doc realtest/editbench/ahi_script.txt \
      --questions realtest/editbench/questions_ahi.json \
      --keys realtest/editbench/keys.local.json \
      --tree-budget 16000 --retr-budget 20 --answer-temp 0.7 --judge \
      --out realtest/editbench/out/ahi_tree_replay_b16
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from llmclient import make_client                       # noqa: E402
from realengine import _load_encoder                    # noqa: E402
from arms import make_arm                               # noqa: E402
from docbench import (SYSTEM, run_qa_multi, run_retell,  # noqa: E402
                      aggregate, arm_total_stats)
import judge as judge_mod                               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-from", required=True, help="含树 gzip 快照的 trees 目录")
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--scenario", default="ahi")
    ap.add_argument("--doc", default=os.path.join(_HERE, "ahi_script.txt"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "questions_ahi.json"))
    ap.add_argument("--keys", default=os.path.join(_HERE, "keys.local.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--tree-budget", type=int, default=16000)
    ap.add_argument("--retr-budget", type=int, default=20)
    ap.add_argument("--provider", default="deepseek")
    ap.add_argument("--model", default="")
    ap.add_argument("--answer-temp", type=float, default=0.7)
    ap.add_argument("--distill-temp", type=float, default=0.0)
    ap.add_argument("--judge-temp", type=float, default=0.0)
    ap.add_argument("--judge", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = args.provider
    if "deepseek" in cfg:
        cfg["deepseek"]["temperature"] = 0.0
        if args.model:
            cfg["deepseek"]["model"] = args.model
    budget = args.tree_budget
    retr_budget = args.retr_budget

    keymap = json.load(open(args.keys, encoding="utf-8")) if args.keys and os.path.exists(args.keys) else {}

    def client_for(tag, temp):
        k = keymap.get(tag)
        c = json.loads(json.dumps(cfg))
        if k:
            c["deepseek"]["api_key"] = k
        c["deepseek"]["temperature"] = float(temp)
        return make_client(c)

    qspec = json.load(open(args.questions, encoding="utf-8"))
    questions = qspec["questions"]
    checklist = qspec.get("retell_checklist", {})
    enc = _load_encoder()

    judge_llm = None
    if args.judge:
        jc = json.loads(json.dumps(cfg))
        jc["deepseek"]["temperature"] = float(args.judge_temp)
        judge_llm = make_client(jc)

    R = args.reuse_from

    def load_arm(label, fname):
        path = os.path.join(R, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        arm = make_arm("tree_v2", client_for(label, args.distill_temp),
                       client_for(label, args.answer_temp), cfg, budget, retr_budget,
                       enc, "", tag=label, build=False)
        arm.load(path)
        return arm

    # 加载三棵终结树（warm = 编辑+冷却后；cold = 无使用史；reb = 新文档重建）
    arms = {
        "inc_tree_v2": load_arm("inc_tree_v2", "tree_v2·inc_final.json.gz"),
        "inc_cold_tree_v2": load_arm("inc_cold_tree_v2", "inc_cold_tree_v2.json.gz"),
        "reb_tree_v2": load_arm("reb_tree_v2", "reb_tree_v2.json.gz"),
    }
    base = load_arm("tree_v2·base", "tree_v2·base.json.gz")

    results = {"model": getattr(client_for("x", 0), "model", "?"), "doc": args.doc,
               "scenario": args.scenario, "n_questions": len(questions),
               "repeats": args.repeats, "answer_temp": args.answer_temp,
               "tree_budget": budget, "retr_budget": retr_budget, "judge": args.judge,
               "reuse_from": R, "phase1": {}, "phase3": {}, "retell": {},
               "tokens": {"phase1": {}, "phase3": {}, "final": {}}, "answers": {}, "qa_pass": {}}
    t0 = time.time()

    def snap(store, key, arm):
        store[key] = arm_total_stats(arm)
        results["tokens"].setdefault("answer", {})[key] = arm.llm.stats() if getattr(arm, "llm", None) else {}
        results["tokens"].setdefault("distill", {})[key] = (arm.distill_meter.stats()
                                                            if getattr(arm, "distill_meter", None) else {})

    print(f"[reuse] {R} | budget={budget} retr={retr_budget} | {len(questions)} 题 × {args.repeats}",
          flush=True)

    # Phase 1：基线（原始文档，从 base 快照）
    print("\n===== Phase 1：基线 Q/A（原始文档，复用 base 快照）=====", flush=True)
    bs = base.snapshot()
    runs = run_qa_multi(base, questions, "before", args.repeats, judge_llm, snap=bs)
    results["phase1"]["tree_v2"] = aggregate("tree_v2", runs)
    results["answers"]["tree_v2·p1"] = {r["id"]: [run[i]["answer"] for run in runs]
                                        for i, r in enumerate(runs[0])}
    results["qa_pass"]["tree_v2·p1"] = {r["id"]: [int(run[i]["pass"]) for run in runs]
                                        for i, r in enumerate(runs[0])}
    v = results["phase1"]["tree_v2"]
    print(f"  tree_v2 pass={v['pass']}/{v['n']} mean={v.get('mean_rate',0):.0%} {v['layers']}", flush=True)
    snap(results["tokens"]["phase1"], "tree_v2·base", base)

    # Phase 3：三臂作答（从各自终结树快照）
    print("\n===== Phase 3：三臂 Q/A（复用终结树）=====", flush=True)
    sn = {}
    for aname, arm in arms.items():
        sn[aname] = arm.snapshot()
        runs = run_qa_multi(arm, questions, "after", args.repeats, judge_llm, snap=sn[aname])
        results["phase3"][aname] = aggregate(aname, runs)
        results["answers"][f"{aname}·p3"] = {r["id"]: [run[i]["answer"] for run in runs]
                                             for i, r in enumerate(runs[0])}
        results["qa_pass"][f"{aname}·p3"] = {r["id"]: [int(run[i]["pass"]) for run in runs]
                                             for i, r in enumerate(runs[0])}
        v = results["phase3"][aname]
        print(f"  {aname:18s} pass={v['pass']}/{v['n']} mean={v.get('mean_rate',0):.0%} {v['layers']}",
              flush=True)
        snap(results["tokens"]["phase3"], aname, arm)

    # Phase 4：复述
    print("\n===== Phase 4：复述 =====", flush=True)
    for aname, arm in arms.items():
        texts = []
        for _ in range(max(1, args.repeats)):
            if sn[aname]:
                arm.restore(sn[aname])
            texts.append(run_retell(arm))
        from docscore import score_retell
        runs_sc = [score_retell(t, checklist) for t in texts]
        last = runs_sc[-1]
        last["answer"] = texts[-1]
        last["per_repeat_coverage"] = [s["coverage_ratio"] for s in runs_sc]
        last["per_repeat_rot"] = [s["rot_ratio"] for s in runs_sc]
        if judge_llm is not None:
            last["judge_rot"] = judge_mod.judge_rot(checklist.get("forbid", []), texts[-1], judge_llm)
            last["judge_hit"] = judge_mod.judge_retell(checklist.get("require", []), texts[-1], judge_llm)
        results["retell"][aname] = last
        with open(os.path.join(args.out, f"retell_{aname}.txt"), "w", encoding="utf-8") as f:
            f.write(texts[-1])
        cov = sum(last["per_repeat_coverage"]) / len(runs_sc)
        rot = sum(last["per_repeat_rot"]) / len(runs_sc)
        print(f"  {aname:18s} 覆盖均值 {cov:.0%} 旧残留 {rot:.0%} ({last['chars']} 字) 漏={last['new_miss']}",
              flush=True)

    for aname, arm in arms.items():
        snap(results["tokens"]["final"], aname, arm)
    results["elapsed_s"] = round(time.time() - t0, 1)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n[done] {results['elapsed_s']}s → {args.out}/results.json", flush=True)


if __name__ == "__main__":
    main()
