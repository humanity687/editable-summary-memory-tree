#!/usr/bin/env python3
"""retell_only.py — 只重跑 Phase4 复述（改 prompt / max_tokens 后无需重跑 Q/A）。

tree 臂从已存 gzip 检查点加载（base / inc_final / reb / inc_cold）；
flat / full 从 doc+ledger 重建（append-only 臂无检查点）。
评分用 questions_<name>.json 的 retell_checklist（支持别名）。

用法：
  python3 realtest/editbench/retell_only.py --name D3_platform \
      --reuse realtest/editbench/out/corpus_run2/D3_platform \
      --out   realtest/editbench/out/corpus_run2/D3_platform/retell2 --repeats 5
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

from llmclient import make_client                     # noqa: E402
from realengine import _load_encoder                  # noqa: E402
from arms import make_arm                             # noqa: E402
from editstorm import build_scenario_spec             # noqa: E402
from docscore import score_retell                     # noqa: E402
from docbench import run_retell                       # noqa: E402

TREE_ARMS = ("inc_tree_v2", "inc_cold_tree_v2", "reb_tree_v2")   # 从检查点加载
SIMPLE = ("flat", "full")                                        # 重建


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--reuse", required=True, help="含 trees/ 的既有 run 目录（加载 tree 检查点）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--keys", default=os.path.join(_HERE, "keys.local.json"))
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--answer-temp", type=float, default=0.7)
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--retr-budget", type=int, default=20)
    ap.add_argument("--splitter", default="line")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    spec = json.load(open(os.path.join(_HERE, "specs", f"{args.name}.json"), encoding="utf-8"))
    qspec = json.load(open(os.path.join(_HERE, f"questions_{args.name}.json"), encoding="utf-8"))
    checklist = qspec["retell_checklist"]
    doc = open(os.path.join(_ROOT, spec["doc"]), encoding="utf-8").read()
    new_doc, ledger = build_scenario_spec(doc, spec)

    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = "deepseek"
    keymap = json.load(open(args.keys, encoding="utf-8")) if os.path.exists(args.keys) else {}

    def client_for(tag):
        c = json.loads(json.dumps(cfg))
        if keymap.get(tag):
            c["deepseek"]["api_key"] = keymap[tag]
        c["deepseek"]["temperature"] = args.answer_temp
        return make_client(c)

    enc = _load_encoder()
    tree_dir = os.path.join(args.reuse, "trees")
    results = {"name": args.name, "reuse": args.reuse, "repeats": args.repeats,
               "retell": {}}

    def run_arm(arm, tag, snap_doc=None):
        texts = []
        for _ in range(args.repeats):
            texts.append(run_retell(arm))
        sc = score_retell(texts[-1], checklist)
        sc["per_repeat_coverage"] = [score_retell(t, checklist)["coverage_ratio"] for t in texts]
        with open(os.path.join(args.out, f"retell2_{tag}.txt"), "w", encoding="utf-8") as f:
            f.write(texts[-1])
        return sc

    # ── tree 臂：从检查点加载 ──
    ckpt = {
        "inc_tree_v2": "tree_v2·inc_final.json.gz",
        "inc_cold_tree_v2": "inc_cold_tree_v2.json.gz",
        "reb_tree_v2": "reb_tree_v2.json.gz",
    }
    for armname, fn in ckpt.items():
        path = os.path.join(tree_dir, fn)
        if not os.path.exists(path):
            print(f"  [skip] {armname}: 缺检查点 {fn}")
            continue
        arm = make_arm("tree_v2", client_for(armname), client_for(armname), cfg,
                       args.budget, args.retr_budget, enc, "", tag=armname, build=False,
                       splitter=args.splitter)
        arm.load(path)
        sc = run_arm(arm, armname)
        results["retell"][armname] = sc
        print(f"  {armname:16s} rec={sc['coverage_record']} nonrec={sc['coverage_nonrecord']} "
              f"rot={sc['rot_ratio']} ({sc['chars']}字)")

    # ── flat / full：重建（inc=doc+ledger，reb=new_doc，base=doc）──
    for name in SIMPLE:
        for phase, src in (("inc", doc), ("reb", new_doc), ("base", doc)):
            arm = make_arm(name, client_for(f"{phase}_{name}"), client_for(f"{phase}_{name}"),
                           cfg, args.budget, args.retr_budget, enc, src, tag=f"{phase}_{name}",
                           splitter=args.splitter)
            if phase == "inc":
                arm.apply_ledger(ledger)
                arm.settle()
            tag = f"{phase}_{name}"
            sc = run_arm(arm, tag)
            results["retell"][tag] = sc
            print(f"  {tag:16s} rec={sc['coverage_record']} nonrec={sc['coverage_nonrecord']} "
                  f"rot={sc['rot_ratio']} ({sc['chars']}字)")
        arm = None

    with open(os.path.join(args.out, "retell2.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"[done] → {args.out}/retell2.json")


if __name__ == "__main__":
    main()
