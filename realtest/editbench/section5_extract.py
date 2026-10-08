#!/usr/bin/env python3
"""section5_extract.py — 从 results.json 提取论文第 5 节「实验」待补数字，输出纯文本。

来源（均为当前代码同轮运行，除标注"存档"者）：
  C1  = out/seed2_r3_8arm_cool4
  C2  = out/ahi_r5_par            （主表口径；第二 run 见 ahi_strong/*）
  C3–C5 = out/corpus_run3/{D1_design,D2_experiments,D3_platform}
  稳健性 = out/ahi_strong/*
用法：python3 realtest/editbench/section5_extract.py --out <路径.txt>
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def load(run):
    p = os.path.join(OUT, run, "results.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def mean(v):
    if not v:
        return None
    xs = [x for x in v if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def pct(x):
    return "—" if x is None else f"{x*100:.1f}%"


def cell(d, phase, arm):
    r = (d.get(phase) or {}).get(arm)
    if not r:
        return "—"
    lay = r.get("layers", {})
    return f"{r['pass']}/{r['n']} ({r['pass_rate']*100:.1f}%) rev={lay.get('reversal','—')}"


def rev(d, phase, arm):
    r = (d.get(phase) or {}).get(arm)
    return r["layers"].get("reversal", "—") if r else "—"


def rt(d, arm):
    r = (d.get("retell") or {}).get(arm, {}) or {}
    return r


def rt_line(d, arm):
    r = rt(d, arm)
    return f"cov={pct(mean(r.get('per_repeat_coverage')))} rot={pct(mean(r.get('per_repeat_rot')))}"


def sig_rows(run):
    for name in ("significance_judge.csv", "significance.csv"):
        p = os.path.join(OUT, run, name)
        if os.path.exists(p):
            return list(csv.DictReader(open(p, encoding="utf-8-sig")))
    return []


C1, C2 = load("seed2_r3_8arm_cool4"), load("ahi_r5_par")
D1, D2, D3 = load("corpus_run3/D1_design"), load("corpus_run3/D2_experiments"), load("corpus_run3/D3_platform")
STR = ["full", "flat", "raptor", "tree_v2"]
NAME = {"full": "全文", "flat": "平面检索", "raptor": "层次摘要", "tree_v2": "本文结构"}

L = []
w = L.append

w("Paper 1 · 第 5 节「实验」待补数据（从 results.json 提取）")
w("=" * 78)
w("来源（同轮 / 当前代码，另行标注者除外）：")
w("  C1 = out/seed2_r3_8arm_cool4   C2 = out/ahi_r5_par（主表口径）")
w("  C3–C5 = out/corpus_run3/{D1_design,D2_experiments,D3_platform}")
w("  稳健性 = out/ahi_strong/*   配对显著性 = 各目录 significance.csv")
w("说明：复述覆盖/残留取各遍均值；反转层 = pass/n；新 run 的 qa_pass 含 judge 复核。")
w("")

w("表 6　编辑正确性主表（增量臂，五语料）")
w("-" * 78)
w(f"{'语料':<12}{'结构':<10}{'反转层':<10}{'复述覆盖':<10}{'复述残留':<10}")
for tag, d in [("C1(N=3)", C1), ("C2(N=5)", C2), ("C3(N=5)", D1), ("C4(N=5)", D2), ("C5(N=5)", D3)]:
    for s in STR:
        a = f"inc_{s}"
        r = rt(d, a)
        w(f"{tag:<12}{NAME[s]:<10}{rev(d,'phase3',a):<10}"
          f"{pct(mean(r.get('per_repeat_coverage'))):<10}{pct(mean(r.get('per_repeat_rot'))):<10}")
    w("")
w("注：tree 增量五语料残留均 0%；append-only 增量残留 C1 33–78%、C2 14–20%、C3 42%、C4 60%、C5 52%。")
w("")

w("表 7　因子分解（C1 N=3 / C2 N=5）")
w("-" * 78)
w(f"{'臂':<18}{'C1 反转':<10}{'C1 覆盖/残留':<18}{'C2 反转':<10}{'C2 覆盖/残留':<18}")
for s in STR:
    for pre in ("inc", "reb"):
        a = f"{pre}_{s}"
        r1, r2 = rt(C1, a), rt(C2, a)
        w(f"{pre+' '+NAME[s]:<18}{rev(C1,'phase3',a):<10}"
          f"{pct(mean(r1.get('per_repeat_coverage')))}/{pct(mean(r1.get('per_repeat_rot'))):<8}"
          f"{rev(C2,'phase3',a):<10}"
          f"{pct(mean(r2.get('per_repeat_coverage')))}/{pct(mean(r2.get('per_repeat_rot'))):<8}")
a = "inc_cold_tree_v2"
r1, r2 = rt(C1, a), rt(C2, a)
w(f"{'inc_cold 本文':<18}{rev(C1,'phase3',a):<10}"
  f"{pct(mean(r1.get('per_repeat_coverage')))}/{pct(mean(r1.get('per_repeat_rot'))):<8}"
  f"{rev(C2,'phase3',a):<10}{pct(mean(r2.get('per_repeat_coverage')))}/{pct(mean(r2.get('per_repeat_rot'))):<8}")
w("")

w("表 8　阶段一基线（编辑前，总通过率 / 反转层）")
w("-" * 78)
w(f"{'语料':<6}" + "".join(f"{NAME[s]:<26}" for s in STR))
for tag, d in [("C1", C1), ("C2", C2), ("C3", D1), ("C4", D2), ("C5", D3)]:
    cells = []
    for s in STR:
        r = (d.get("phase1") or {}).get(s)
        cells.append(f"{r['pass']}/{r['n']} ({r['pass_rate']*100:.1f}%) 反{r['layers'].get('reversal','—')}"
                     if r else "—")
    w(f"{tag:<6}" + "".join(f"{c:<26}" for c in cells))
w("注：C2 主口径 = ahi_r5_par。第二 run（ahi_strong/baseline，tree）：P1 171/205 (83.4%) 反 64/85。")
w("")

w("表 9　稳健性（C2，N=5；P1=总/反转；inc=总/反转；残留=复述旧值残留均值）")
w("-" * 78)
conds = [
    ("全文·基础(存档)", "ahi_r5", "full", "inc_full"),
    ("全文·思考", "ahi_strong/think_full", "full", "inc_full"),
    ("平面·基础", "ahi_strong/baseline_flat", "flat", "inc_flat"),
    ("平面·思考", "ahi_strong/think_flat", "flat", "inc_flat"),
    ("平面·大向量", "ahi_strong/bgelarge_flat", "flat", "inc_flat"),
    ("本文·基础", "ahi_strong/baseline", "tree_v2", "inc_tree_v2"),
    ("本文·思考", "ahi_strong/think_tree", "tree_v2", "inc_tree_v2"),
    ("本文·大向量", "ahi_strong/bgelarge", "tree_v2", "inc_tree_v2"),
]
for label, run, s, ia in conds:
    d = load(run)
    p1 = d.get("phase1", {}).get(s)
    inc = d.get("phase3", {}).get(ia)
    r = rt(d, ia)
    p1s = f"P1 {p1['pass']}/{p1['n']},反{p1['layers'].get('reversal','—').split('/')[0]}" if p1 else "P1 —"
    incs = f"inc {inc['pass']}/{inc['n']},反{inc['layers'].get('reversal','—').split('/')[0]}" if inc else "inc —"
    w(f"{label:<18}{p1s:<24}{incs:<24}残留 {pct(mean(r.get('per_repeat_rot')))}")
w("")

w("表 10　成本分列（C2 = ahi_r5_par 同轮；C1 = seed2 同轮）")
w("-" * 78)
et = C2.get("edit_time", {})
et1 = C1.get("edit_time", {})
w("编辑路径延迟 / 推迟维护 settle：")
for s in STR:
    e = et.get(s, {})
    e1 = et1.get(s, {})
    w(f"  {NAME[s]:<8} C1 {e1.get('edit_ms','—')} ms / {e1.get('settle_ms','—')} ms   "
      f"C2 {e.get('edit_ms','—')} ms / {e.get('settle_ms','—')} ms")
w("")
def usage_sum(d, key):
    tot = {"prompt_tokens": 0, "completion_tokens": 0}
    for row in (d.get("usage") or {}).get(key, []) or []:
        for k in tot:
            tot[k] += row.get(k, 0) or 0
    return tot

w("成本二分（作答侧来自 usage；其余 = tokens.final − 问答作答）：")
w("  ⚠ tokens.final = 作答+蒸馏+全局复述 合并值；usage 只含问答作答侧。")
w("    故 'final − 问答作答' = 蒸馏 + 复述生成，**不是纯蒸馏**（全文臂无蒸馏，其残差全是复述）。")
w("    维护蒸馏侧 token 未在 results.json 单独落盘 —— 精确拆分需改 docbench 分路记录（待办）。")
for s in STR:
    ia = f"inc_{s}"
    a = usage_sum(C2, f"{s}·p1")
    a3 = usage_sum(C2, ia + "·p3")
    at = {k: a[k] + a3[k] for k in a}
    f = (C2.get("tokens", {}).get("final", {}) or {}).get(ia, {})
    dp = f.get("prompt_tokens", 0) - at["prompt_tokens"]
    dc = f.get("completion_tokens", 0) - at["completion_tokens"]
    w(f"  inc_{NAME[s]:<6} 问答作答 prompt={at['prompt_tokens']:>10} compl={at['completion_tokens']:>7} | "
      f"其余(蒸馏+复述) prompt={dp:>9} compl={dc:>7} | 合并prompt={f.get('prompt_tokens',0):>9} "
      f"KV命中={f.get('cache_hit_rate')}")
w("")

w("配对显著性（McNemar，题×遍；全部含 judge（C1 经 rejudge 对齐））")
w("-" * 78)
for run, tag in [("seed2_r3_8arm_cool4", "C1(judge)"), ("ahi_r5_par", "C2"), 
                 ("corpus_run3/D1_design", "C3"), ("corpus_run3/D2_experiments", "C4"),
                 ("corpus_run3/D3_platform", "C5")]:
    rows = sig_rows(run)
    qa = [r for r in rows if r["metric"] == "qa_pass"]
    w(f"[{tag}]")
    for r in qa:
        w(f"  {r['pair']:<34} {r['rate_A']} vs {r['rate_B']}  Δ{r['delta']}  p={r['p_value']}  ({r['stat']})")
w("")

w("record / nonrecord（C3–C5，预注册两段）")
w("-" * 78)
for tag, d in [("C3", D1), ("C4", D2), ("C5", D3)]:
    w(f"[{tag}]")
    for a in ("inc_tree_v2", "reb_tree_v2", "inc_cold_tree_v2", "inc_full", "inc_flat", "inc_raptor"):
        r = rt(d, a)
        if r:
            w(f"  {a:<16} record={r.get('coverage_record')} nonrecord={r.get('coverage_nonrecord')} "
              f"cov={pct(mean(r.get('per_repeat_coverage')))} rot={pct(mean(r.get('per_repeat_rot')))}")
w("")

w("复述零残留逐遍核验（各臂 per_repeat_rot）")
w("-" * 78)
for tag, d in [("C1", C1), ("C2", C2), ("C3", D1), ("C4", D2), ("C5", D3)]:
    w(f"[{tag}]")
    for a in sorted((d.get("retell") or {}).keys()):
        r = rt(d, a)
        pr = r.get("per_repeat_rot")
        if pr is not None:
            w(f"  {a:<18} rot/遍 = {pr}")
w("")

w("仍需新跑（提取不可得）")
w("-" * 78)
w("  1. 同代码的 full/flat 基础臂（表 9 两处“存档”星号）")
w("  2. C2 本文结构第三 run（方差升级为均值±std；现双 run：68/85 vs 84/85）")
w("  3. C2 A/B 同代码重跑（现为旧占位口径）")
w("  4. C1 若需 judge 口径的 McNemar（现为确定性口径，需重判失败样本）")

txt = "\n".join(L) + "\n"
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
args = ap.parse_args()
with open(args.out, "w", encoding="utf-8") as f:
    f.write(txt)
print(f"[done] {args.out}  ({len(txt):,} chars)")
