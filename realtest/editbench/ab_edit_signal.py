#!/usr/bin/env python3
"""ab_edit_signal.py — 编辑"使用信号(热) vs 障碍(冷)"的 matched-pair A/B。

关键：**同一棵 pre-edit 树**（base 或 warm 快照）分别施加 on/off 的编辑，
唯一差异 = 编辑新原子的 HP（热=当使用信号入窗；冷=当障碍）。排除跨运行/构建噪声。

四臂 = {warm, cold} × {signal(on), obstacle(off)}；reb 作参考。
  warm 源 = base 建树 → Phase1 使用（一次）→ 快照
  cold 源 = base 建树（未使用）→ 快照

用法：
  python3 realtest/editbench/ab_edit_signal.py --scenario seed2 \
      --doc realtest/seed2/story.txt --questions realtest/editbench/questions_full.json \
      --keys realtest/editbench/keys.local.json --tree-budget 7000 --repeats 3 \
      --answer-temp 0.7 --judge --out realtest/editbench/out/seed2_ab_editsignal
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
from docbench import (run_qa_multi, run_retell, aggregate,  # noqa: E402
                      arm_total_stats, SCENARIOS)
from docscore import score_retell                       # noqa: E402
import judge as judge_mod                               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--scenario", default="seed2", choices=list(SCENARIOS))
    ap.add_argument("--doc", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--keys", default=os.path.join(_HERE, "keys.local.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--tree-budget", type=int, default=7000)
    ap.add_argument("--retr-budget", type=int, default=6)
    ap.add_argument("--provider", default="deepseek")
    ap.add_argument("--model", default="")
    ap.add_argument("--answer-temp", type=float, default=0.7)
    ap.add_argument("--distill-temp", type=float, default=0.0)
    ap.add_argument("--judge-temp", type=float, default=0.0)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--skip-retell", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = args.provider
    if "deepseek" in cfg:
        cfg["deepseek"]["temperature"] = 0.0
        if args.model:
            cfg["deepseek"]["model"] = args.model
    keymap = json.load(open(args.keys, encoding="utf-8")) if args.keys and os.path.exists(args.keys) else {}

    def client_for(tag, temp):
        k = keymap.get(tag)
        c = json.loads(json.dumps(cfg))
        if k:
            c["deepseek"]["api_key"] = k
        c["deepseek"]["temperature"] = float(temp)
        return make_client(c)

    doc = open(args.doc, encoding="utf-8").read().strip()
    new_doc, ledger = SCENARIOS[args.scenario](doc)
    qspec = json.load(open(args.questions, encoding="utf-8"))
    questions = qspec["questions"]
    checklist = qspec.get("retell_checklist", {})
    enc = _load_encoder()
    judge_llm = None
    if args.judge:
        jc = json.loads(json.dumps(cfg))
        jc["deepseek"]["temperature"] = float(args.judge_temp)
        judge_llm = make_client(jc)

    def new_tree(tag, edit_signal=True, build=True, d=""):
        return make_arm("tree_v2", client_for(tag, args.distill_temp),
                        client_for(tag, args.answer_temp), cfg, args.tree_budget,
                        args.retr_budget, enc, d, tag=tag, build=build,
                        edit_signal=edit_signal)

    results = {"scenario": args.scenario, "doc": args.doc, "repeats": args.repeats,
               "answer_temp": args.answer_temp, "tree_budget": args.tree_budget,
               "n_questions": len(questions), "phase1": {}, "phase3": {}, "retell": {},
               "answers": {}, "tokens": {}}
    t0 = time.time()

    # ── base 树（建一次）+ 基线 QA（= 使用史，一遍）──
    print("===== base 建树 + 基线 Q/A（使用一遍）=====", flush=True)
    base = new_tree("tree_v2·base", d=doc)
    base_snap = base.snapshot()                       # pre-usage
    runs = run_qa_multi(base, questions, "before", args.repeats, judge_llm, snap=base_snap)
    results["phase1"]["base"] = aggregate("base", runs)
    warm_snap = base.snapshot()                       # post-usage（pre-edit）
    v = results["phase1"]["base"]
    print(f"  base {v['pass']}/{v['n']} {v['layers']}", flush=True)

    # ── reb 参考臂 ──
    reb = new_tree("reb_tree_v2", d=new_doc)
    reb_snap = reb.snapshot()

    arms = {}
    print("\n===== A/B：{warm,cold} × {signal,obstacle} 同源施加编辑 =====", flush=True)
    for kind, src in (("warm", warm_snap), ("cold", base_snap)):
        for mode, tag in (("signal", "on"), ("obstacle", "off")):
            name = f"{kind}_{mode}"
            arm = new_tree(f"{name}_tree", edit_signal=(mode == "signal"), build=False)
            arm.load(src)                             # 同一 pre-edit 树
            arm.apply_ledger(ledger)
            arm.settle()
            sn = arm.snapshot()
            runs = run_qa_multi(arm, questions, "after", args.repeats, judge_llm, snap=sn)
            results["phase3"][name] = aggregate(name, runs)
            results["answers"][name] = {r["id"]: [run[i]["answer"] for run in runs]
                                        for i, r in enumerate(runs[0])}
            results.setdefault("_snaps", {})[name] = sn
            v = results["phase3"][name]
            print(f"  {name:16s} {v['pass']}/{v['n']} {v['layers']}", flush=True)

    # reb 参考
    runs = run_qa_multi(reb, questions, "after", args.repeats, judge_llm, snap=reb_snap)
    results["phase3"]["reb"] = aggregate("reb", runs)
    results["answers"]["reb"] = {r["id"]: [run[i]["answer"] for run in runs]
                                 for i, r in enumerate(runs[0])}
    print(f"  reb              {results['phase3']['reb']['pass']}/{results['phase3']['reb']['n']} "
          f"{results['phase3']['reb']['layers']}", flush=True)

    # ── 复述 ──
    if not args.skip_retell:
        print("\n===== 复述 =====", flush=True)
        for name in list(results["phase3"]):
            arm = None
            if name == "reb":
                arm = reb
            else:
                arm = new_tree(f"{name}_tree", edit_signal=(name.endswith("signal")), build=False)
                arm.load(results["_snaps"][name])
            texts = []
            for _ in range(max(1, args.repeats)):
                texts.append(run_retell(arm))
            sc = [score_retell(t, checklist) for t in texts]
            last = sc[-1]
            last["per_repeat_coverage"] = [s["coverage_ratio"] for s in sc]
            last["per_repeat_rot"] = [s["rot_ratio"] for s in sc]
            results["retell"][name] = last
            cov = sum(last["per_repeat_coverage"]) / len(sc)
            rot = sum(last["per_repeat_rot"]) / len(sc)
            print(f"  {name:16s} 覆盖 {cov:.0%} 残留 {rot:.0%} ({last['chars']}字) 漏={last['new_miss']}",
                  flush=True)

    results.pop("_snaps", None)
    results["elapsed_s"] = round(time.time() - t0, 1)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n[done] {results['elapsed_s']}s → {args.out}/results.json", flush=True)


if __name__ == "__main__":
    main()
