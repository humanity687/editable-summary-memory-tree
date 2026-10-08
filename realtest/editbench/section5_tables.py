#!/usr/bin/env python3
"""section5_tables.py — 把论文第 5 节的所有表导出为 CSV（UTF-8 BOM，便于插入 Excel/Word）。

用法：python3 realtest/editbench/section5_tables.py --outdir ~/Downloads/material/csv
产出：table6 / table7 / table8 / table9 / table10 / significance / ab_editsignal / record_nonrecord
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
STR = ["full", "flat", "raptor", "tree_v2"]
CN = {"full": "全文", "flat": "平面检索", "raptor": "层次摘要", "tree_v2": "本文结构"}


def load(run):
    p = os.path.join(OUT, run, "results.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def mean(v):
    v = v or []
    xs = [x for x in v if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def rget(d, arm):
    return (d.get("retell") or {}).get(arm, {}) or {}


def rev(d, phase, arm):
    r = (d.get(phase) or {}).get(arm)
    return r["layers"].get("reversal") if r else None


def total(d, phase, arm):
    r = (d.get(phase) or {}).get(arm)
    return r["pass"] if r else None


C1 = load("seed2_r3_8arm_cool4")
C2 = load("ahi_r5_par")
C3, C4, C5 = (load(f"corpus_run3/{x}") for x in ("D1_design", "D2_experiments", "D3_platform"))


def dump(name, header, rows, outdir):
    p = os.path.join(outdir, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name}: {len(rows)} 行")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    D = args.outdir

    # 表 6 编辑正确性主表（增量臂）
    rows = []
    for tag, d in [("C1", C1), ("C2", C2), ("C3", C3), ("C4", C4), ("C5", C5)]:
        for s in STR:
            a = f"inc_{s}"
            rt = rget(d, a)
            rows.append([tag, CN[s], rev(d, "phase3", a),
                         mean(rt.get("per_repeat_coverage")), mean(rt.get("per_repeat_rot"))])
    dump("table6_edit_correctness.csv",
         ["语料", "结构", "反转层", "复述覆盖", "复述残留"], rows, D)

    # 表 7 因子分解
    rows = []
    for s in STR:
        for pre in ("inc", "reb"):
            a = f"{pre}_{s}"
            r1, r2 = rget(C1, a), rget(C2, a)
            rows.append([f"{pre} {CN[s]}", rev(C1, "phase3", a), mean(r1.get("per_repeat_coverage")),
                         mean(r1.get("per_repeat_rot")), rev(C2, "phase3", a),
                         mean(r2.get("per_repeat_coverage")), mean(r2.get("per_repeat_rot"))])
    a = "inc_cold_tree_v2"
    rows.append(["inc_cold 本文", rev(C1, "phase3", a), mean(rget(C1, a).get("per_repeat_coverage")),
                 mean(rget(C1, a).get("per_repeat_rot")), rev(C2, "phase3", a),
                 mean(rget(C2, a).get("per_repeat_coverage")), mean(rget(C2, a).get("per_repeat_rot"))])
    dump("table7_factor_decomposition.csv",
         ["臂", "C1反转", "C1覆盖", "C1残留", "C2反转", "C2覆盖", "C2残留"], rows, D)

    # 表 8 阶段一基线（长表）
    rows = []
    for tag, d in [("C1", C1), ("C2", C2), ("C3", C3), ("C4", C4), ("C5", C5)]:
        for s in STR:
            r = (d.get("phase1") or {}).get(s)
            if r:
                rows.append([tag, CN[s], r["pass"], r["n"], round(r["pass_rate"], 4),
                             r["layers"].get("reversal")])
    dump("table8_phase1_baseline.csv",
         ["语料", "结构", "通过", "总数", "通过率", "反转层"], rows, D)

    # 表 9 稳健性
    conds = [
        ("全文", "基础(存档)", "ahi_r5", "full", "inc_full"),
        ("全文", "思考", "ahi_strong/think_full", "full", "inc_full"),
        ("平面检索", "基础", "ahi_strong/baseline_flat", "flat", "inc_flat"),
        ("平面检索", "思考", "ahi_strong/think_flat", "flat", "inc_flat"),
        ("平面检索", "大向量", "ahi_strong/bgelarge_flat", "flat", "inc_flat"),
        ("本文结构", "基础", "ahi_strong/baseline", "tree_v2", "inc_tree_v2"),
        ("本文结构", "思考", "ahi_strong/think_tree", "tree_v2", "inc_tree_v2"),
        ("本文结构", "大向量", "ahi_strong/bgelarge", "tree_v2", "inc_tree_v2"),
    ]
    rows = []
    for struct, enh, run, s, ia in conds:
        d = load(run)
        p1 = (d.get("phase1") or {}).get(s)
        inc = (d.get("phase3") or {}).get(ia)
        rows.append([struct, enh,
                     p1["pass"] if p1 else None, p1["layers"].get("reversal") if p1 else None,
                     inc["pass"] if inc else None, inc["layers"].get("reversal") if inc else None,
                     mean(rget(d, ia).get("per_repeat_rot"))])
    dump("table9_robustness.csv",
         ["结构", "增强", "P1总", "P1反转", "inc总", "inc反转", "inc残留"], rows, D)

    # 表 10 成本
    et1, et2 = C1.get("edit_time", {}), C2.get("edit_time", {})
    def usum(d, key):
        t = {"prompt_tokens": 0, "completion_tokens": 0}
        for row in (d.get("usage") or {}).get(key, []) or []:
            for k in t:
                t[k] += row.get(k, 0) or 0
        return t
    rows = []
    for s in STR:
        ia = f"inc_{s}"
        a = usum(C2, f"{s}·p1"); a3 = usum(C2, ia + "·p3")
        ap = a["prompt_tokens"] + a3["prompt_tokens"]
        f = (C2.get("tokens", {}).get("final", {}) or {}).get(ia, {})
        rows.append([CN[s],
                     et1.get(s, {}).get("edit_ms"), et1.get(s, {}).get("settle_ms"),
                     et2.get(s, {}).get("edit_ms"), et2.get(s, {}).get("settle_ms"),
                     ap, f.get("prompt_tokens", 0) - ap, f.get("completion_tokens", 0) - a["completion_tokens"] - a3["completion_tokens"],
                     f.get("cache_hit_rate")])
    dump("table10_cost.csv",
         ["结构", "编辑_C1_ms", "settle_C1_ms", "编辑_C2_ms", "settle_C2_ms",
          "作答prompt_C2", "维护prompt_C2", "维护completion_C2", "KV命中_C2"], rows, D)

    # 显著性（汇总所有语料）
    rows = []
    for run, tag in [("seed2_r3_8arm_cool4", "C1"), ("ahi_r5_par", "C2"),
                     ("corpus_run3/D1_design", "C3"), ("corpus_run3/D2_experiments", "C4"),
                     ("corpus_run3/D3_platform", "C5")]:
        for nm in ("significance_judge.csv", "significance.csv"):
            p = os.path.join(OUT, run, nm)
            if os.path.exists(p):
                for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                    if r["metric"] == "qa_pass":
                        rows.append([tag, r["pair"], r["rate_A"], r["rate_B"], r["delta"],
                                     r["test"], r["stat"], r["p_value"]])
                break
    dump("significance.csv",
         ["语料", "对比", "A率", "B率", "Δ", "检验", "stat", "p"], rows, D)

    # A/B 编辑信号
    rows = []
    ab_runs = [("C2同代码", "ahi_ab_editsignal_sc"), ("C2旧占位", "ahi_ab_editsignal"),
               ("C1", "seed2_ab_editsignal")]
    for tag, run in ab_runs:
        d = load(run)
        for a in ("warm_signal", "warm_obstacle", "cold_signal", "cold_obstacle", "reb"):
            r = (d.get("phase3") or {}).get(a)
            if not r:
                continue
            rt = rget(d, a)
            rows.append([tag, a, r["pass"], r["n"], r["layers"].get("reversal"),
                         mean(rt.get("per_repeat_coverage")), mean(rt.get("per_repeat_rot"))])
    dump("ab_editsignal.csv",
         ["口径", "臂", "总", "n", "反转", "复述覆盖", "复述残留"], rows, D)

    # record/nonrecord
    rows = []
    for tag, d in [("C3", C3), ("C4", C4), ("C5", C5)]:
        for a in ("inc_tree_v2", "reb_tree_v2", "inc_cold_tree_v2", "inc_full", "inc_flat", "inc_raptor"):
            rt = rget(d, a)
            if rt:
                rows.append([tag, a, rt.get("coverage_record"), rt.get("coverage_nonrecord"),
                             mean(rt.get("per_repeat_coverage")), mean(rt.get("per_repeat_rot"))])
    dump("record_nonrecord.csv",
         ["语料", "臂", "record", "nonrecord", "覆盖", "残留"], rows, D)

    # 完成情况 / 说明
    rows = [
        ["C2 第三 run", "✅ 已完成", "tree 臂经 gzip 零重建重放（暖 53/85）；full/flat/raptor 丢失但不涉及反转方差结论"],
        ["C2 A/B 同代码", "✅ 已完成（ahi_ab_editsignal_sc）", "signal ≥ obstacle（暖/冷树）"],
        ["表 10 精确“蒸馏 vs 作答”分路", "旧 run 未记录", "代码已加装分路落盘，随下次 run 生效"],
        ["C1 judge 口径", "✅ 已补（results_qajudge.json，334 次 judge）", "—"],
    ]
    dump("status.csv", ["项", "状态", "可救性"], rows, D)

    print(f"[done] → {D}")


if __name__ == "__main__":
    main()
