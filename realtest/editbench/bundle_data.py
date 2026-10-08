#!/usr/bin/env python3
"""bundle_data.py — 把指定 run 的 results.json + significance.csv 与实验报告拼成单个 txt，
每节标注来源路径与修改时间；文件头给出清单与口径提醒。

用法:
  python3 realtest/editbench/bundle_data.py \
    --out ~/Downloads/material/编辑基准数据汇总.txt \
    --report ~/Downloads/material/实验设计报告.md \
    --report ~/Downloads/material/数据报告.md \
    --report realtest/editbench/EXPERIMENT_REPORT.md
  # --run 可重复指定 out/ 下的相对目录；不传则用 DEFAULT_RUNS
  # --no-json 只收 significance.csv 与报告（不含体积大的 results.json）
"""
from __future__ import annotations

import argparse
import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(_HERE, "out")

DEFAULT_RUNS = [
    "ahi_r5_par",
    "corpus_run3/D1_design",
    "corpus_run3/D2_experiments",
    "corpus_run3/D3_platform",
    "seed2_r3_8arm_cool4",
]

SEP = "=" * 100


def _mtime(p):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
    except OSError:
        return "?"


def header(kind, src):
    return f"{SEP}\n[{kind}] {src}\n[修改时间] {_mtime(src)}\n{SEP}\n"


def summarize(d):
    return (f"scenario={d.get('scenario')}  model={d.get('model')}  n_questions={d.get('n_questions')}  "
            f"repeats={d.get('repeats')}  answer_temp={d.get('answer_temp')}  "
            f"answer_thinking={d.get('answer_thinking', '?')}  distill_thinking={d.get('distill_thinking', '?')}  "
            f"embed={d.get('embed_model', '?')}  splitter={d.get('splitter', 'sentence')}")


def _trunc(s, n):
    if not isinstance(s, str) or len(s) <= n:
        return s
    return s[:n] + f" …〔已截断，原长 {len(s)} 字〕"


def prune(d):
    """裁掉重复/次要体积：去 usage（与 tokens 聚合重复）；answers / retell.answer 截断。
    保留 qa_pass / phase1 / phase3 / retell 指标 / tokens / edit_time / ledger / trees。"""
    d = dict(d)
    d.pop("usage", None)
    for k, by_id in list((d.get("answers") or {}).items()):
        if isinstance(by_id, dict):
            d["answers"][k] = {q: [_trunc(a, 300) for a in v] for q, v in by_id.items()}
    for a, rt in list((d.get("retell") or {}).items()):
        if isinstance(rt, dict) and "answer" in rt:
            rt["answer"] = _trunc(rt["answer"], 800)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", default=[], help="out 下的相对目录（可重复）")
    ap.add_argument("--report", action="append", default=[], help="报告文件路径（可重复）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-json", action="store_true", help="不含 results.json（只收 significance + 报告）")
    ap.add_argument("--keep-full", action="store_true", help="不裁剪 results.json（默认裁剪：去 usage、截断长答案）")
    args = ap.parse_args()

    runs = args.run or DEFAULT_RUNS
    body, manifest = [], []

    for r in runs:
        rj = os.path.join(OUT_ROOT, r, "results.json")
        sj = os.path.join(OUT_ROOT, r, "significance.csv")
        if os.path.exists(rj) and not args.no_json:
            with open(rj, encoding="utf-8") as f:
                raw = json.load(f)
            d = raw if args.keep_full else prune(raw)
            note = ("" if args.keep_full else
                    "  〔已裁剪：去 usage；answers≤300 字、retell.answer≤800 字；--keep-full 可保留全文〕")
            body.append(header("run / results.json", rj) + summarize(d) + note + "\n" + "-" * 100 + "\n"
                        + json.dumps(d, ensure_ascii=False, indent=1) + "\n\n")
            manifest.append(("results.json" + ("" if args.keep_full else " (pruned)"), rj, os.path.getsize(rj)))
        if os.path.exists(sj):
            body.append(header("run / significance.csv", sj)
                        + open(sj, encoding="utf-8-sig").read() + "\n\n")
            manifest.append(("significance.csv", sj, os.path.getsize(sj)))
        if not os.path.exists(rj) and not os.path.exists(sj):
            body.append(header("run / [缺失]", os.path.join(OUT_ROOT, r)) + "（无 results.json/significance.csv）\n\n")

    for rep in args.report:
        if os.path.exists(rep):
            body.append(header("实验报告", rep) + open(rep, encoding="utf-8").read() + "\n\n")
            manifest.append(("report", rep, os.path.getsize(rep)))
        else:
            body.append(header("实验报告 / [缺失]", rep) + "\n\n")

    cover = [f"Paper 1 编辑基准 · 数据与实验报告汇总（bundle）",
             f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"生成脚本: realtest/editbench/bundle_data.py",
             "",
             "[来源清单]"]
    for kind, src, size in manifest:
        cover.append(f"  - [{kind}] {src}  ({size:,} bytes)")
    cover += [
        "",
        "[口径提醒]",
        "  · 新 run（ahi_r5_par、corpus_run3/D*）的 qa_pass 含 judge 复核，与主表口径一致；",
        "  · 旧 run（seed2_r3_8arm_cool4、旧 ahi_r5）无 qa_pass，significance 为确定性回退（偏严、会低估 tree）；",
        "  · 复述覆盖/残留以各遍均值为准；results.json 内 coverage_ratio 是末遍值。",
        "",
        "#" * 100,
        "",
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(cover) + "".join(body))
    print(f"[done] {args.out}  ({os.path.getsize(args.out):,} bytes, {len(manifest)} 个来源)")


if __name__ == "__main__":
    main()
