#!/usr/bin/env python3
"""merge_results.py — 合并"按结构拆并发"产出的多个 results.json。

背景：docbench.py 的 Phase 1/3/4 是串行循环，8 臂逐个跑很慢。四结构（full/flat/
raptor/tree_v2）**彼此不共享任何状态**（各自 base/inc/reb/cold），因此可拆成 4 个
进程并发：`docbench.py --structures <S> --out <OUT>/<S>`，再合并为单份 results.json。
等价于单进程全臂（跨结构无耦合），但 ~N 倍提速；不改 bench 逻辑，无匹配性/公平性风险。

用法：
  # 目录式：扫描 <indir>/{full,flat,raptor,tree_v2}/results.json
  python3 realtest/editbench/merge_results.py --indir realtest/editbench/out/ahi_r5_par
  # 或显式列出
  python3 realtest/editbench/merge_results.py --inputs a.json b.json --out merged.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STRUCTURES = ("full", "flat", "raptor", "tree_v2")

# 需要按结构并集合并的嵌套字段
_NESTED = ("phase1", "phase3", "retell", "answers", "usage", "edit_time", "trees")
# tokens 的两层嵌套
_TOKENS = ("phase1", "phase3", "final")


def merge_dicts(dicts: list[dict]) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in d.items():
            if k in _NESTED and isinstance(v, dict):
                out.setdefault(k, {}).update(v)
            elif k == "tokens" and isinstance(v, dict):
                dst = out.setdefault(k, {})
                for tk in _TOKENS:
                    dst.setdefault(tk, {}).update(v.get(tk, {}))
            elif isinstance(v, dict):
                # 其它 dict（如不存在）也做浅并
                if isinstance(out.get(k), dict):
                    out[k].update(v)
                else:
                    out[k] = dict(v)
            else:
                out.setdefault(k, v)   # 标量：取首份
    return out


def summarize(merged: dict) -> None:
    print("\n===== 合并汇总（Q/A pass 率）=====")
    for phase in ("phase1", "phase3"):
        if merged.get(phase):
            print(f"  [{phase}]")
            for k, v in merged[phase].items():
                print(f"    {k:16s} {v.get('pass')}/{v.get('n')} "
                      f"mean={v.get('mean_rate', 0):.0%} ±{v.get('std_rate', 0):.0%} "
                      f"每遍={v.get('per_repeat_pass')} {v.get('layers')}")
    if merged.get("retell"):
        print("\n===== 复述（覆盖/残留）=====")
        for k, v in merged["retell"].items():
            cov = v.get("per_repeat_coverage") or [v.get("coverage_ratio", 0)]
            rot = v.get("per_repeat_rot") or [v.get("rot_ratio", 0)]
            print(f"  {k:16s} 覆盖={sum(cov)/len(cov):.0%} 残留={sum(rot)/len(rot):.0%} "
                  f"漏={v.get('new_miss')}")
    if merged.get("edit_time"):
        print("\n===== 编辑时刻成本 =====")
        for k, v in merged["edit_time"].items():
            print(f"  {k:14s} 编辑路径 {v.get('edit_ms'):9.2f} ms | "
                  f"推迟蒸馏 {v.get('settle_ms'):10.2f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indir", default="", help="含 <structure>/results.json 的目录")
    ap.add_argument("--inputs", nargs="*", default=[], help="显式 results.json 列表")
    ap.add_argument("--structures", default=",".join(DEFAULT_STRUCTURES))
    ap.add_argument("--out", default="", help="合并输出路径（默认 <indir>/results.json）")
    args = ap.parse_args()

    paths = list(args.inputs)
    if args.indir:
        for s in args.structures.split(","):
            p = os.path.join(args.indir, s.strip(), "results.json")
            if os.path.exists(p):
                paths.append(p)
            else:
                print(f"[warn] 缺 {p}", file=sys.stderr)
    if not paths:
        ap.error("没有可合并的 results.json（--indir 或 --inputs）")

    dicts = [json.load(open(p, encoding="utf-8")) for p in paths]
    merged = merge_dicts(dicts)
    merged["parallel_structures"] = [os.path.basename(os.path.dirname(p)) or p
                                     for p in paths]
    merged["elapsed_s"] = round(sum(d.get("elapsed_s", 0) or 0 for d in dicts), 1)

    out = args.out or (os.path.join(args.indir, "results.json") if args.indir else "")
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=1)
        print(f"[merged] {len(paths)} 份 → {out}")
    summarize(merged)
    if out:
        print(f"[done] → {out}")


if __name__ == "__main__":
    main()
