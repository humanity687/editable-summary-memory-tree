#!/usr/bin/env python3
"""rejudge_qa.py — 对**无 qa_pass 的旧 run** 的问答失败样本补 LLM judge，
重建 `qa_pass`（使口径与含 judge 的新 run 对齐）。确定性通过者不送 judge。

用法:
  python3 realtest/editbench/rejudge_qa.py \
    --run out/seed2_r3_8arm_cool4/results.json \
    --questions questions_full.json \
    --out out/seed2_r3_8arm_cool4/results_qajudge.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
for _p in (_HERE, _REALTEST, os.path.dirname(_REALTEST)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docscore import check_spec      # noqa: E402
import judge as judge_mod            # noqa: E402
from llmclient import make_client    # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--provider", default="deepseek", help="必须与主实验 judge 一致（默认 deepseek）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = args.provider          # 关键：config.json 默认 ollama(本地)，必须覆写
    cfg["deepseek"]["temperature"] = 0.0
    llm = make_client(cfg)
    d = json.load(open(args.run, encoding="utf-8"))
    qs = {q["id"]: q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]}

    qa, calls, total = {}, 0, 0
    for k, ans_by_id in (d.get("answers") or {}).items():
        phase = "after" if k.endswith("·p3") else "before"
        qa[k] = {}
        for qid, ans_list in ans_by_id.items():
            q = qs.get(qid, {})
            spec = (q.get(f"check_{phase}") or q.get("check_after") or q.get("check_before") or {})
            out = []
            for a in ans_list:
                total += 1
                ok, _ = check_spec(a, spec)
                if not ok and (spec.get("require") or spec.get("forbid")) and llm is not None:
                    calls += 1
                    try:
                        jr = judge_mod.judge_pass(spec.get("require", []), spec.get("forbid", []), a, llm)
                        ok = bool(jr.get("pass"))
                    except Exception as e:  # noqa: BLE001
                        print(f"  [warn] judge 失败 {k}/{qid}: {e}")
                    if calls % 50 == 0:
                        print(f"  judge 调用 {calls}（总样本 {total}）", flush=True)
                out.append(int(ok))
            qa[k][qid] = out
    d["qa_pass"] = qa
    d["_rejudge"] = {"judge_calls": calls, "samples": total}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print(f"[done] {args.out}  judge 调用 {calls} / 样本 {total}")


if __name__ == "__main__":
    main()
