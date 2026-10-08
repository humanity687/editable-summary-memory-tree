#!/usr/bin/env python3
"""significance.py — editbench 配对显著性 / per-repeat 方差报告（本地、零 LLM）。

读取一个或多个 results.json，按题逐遍复算 Q/A pass（docscore.check_spec），并对指定臂对
做配对检验：
  Q/A（二值）  ：McNemar 精确检验（question×repeat 配对） + 每臂分层 pass/n
  Q/A（按题）  ：每题各遍通过率的 Wilcoxon 符号秩检验（配对）
  复述（连续）  ：coverage / rot 的配对 Wilcoxon（按遍配对）
输出 CSV（每对比较一行）+ 终端汇总。

用法：
  python3 realtest/editbench/significance.py \
      --run realtest/editbench/out/seed2_r3_8arm_cool4/results.json \
      --out realtest/editbench/out/seed2_r3_8arm_cool4/significance.csv
  # 多个 run（如 ahi_strong 各条件各一个文件）可重复 --run；arms 自动合并
  # 指定比较：--pairs "inc_tree_v2:reb_tree_v2,inc_tree_v2:inc_full"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np
from scipy import stats

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
for _p in (_HERE, _REALTEST, os.path.dirname(_REALTEST)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from docscore import check_spec  # noqa: E402

_QFILE = {
    "seed2": "questions_full.json",
    "ahi": "questions_ahi.json",
    "D1_design": "questions_D1_design.json",
    "D2_experiments": "questions_D2_experiments.json",
    "D3_platform": "questions_D3_platform.json",
}


def _load_questions(scenario, explicit):
    path = explicit or os.path.join(_HERE, _QFILE.get(scenario, "_missing_"))
    if not os.path.exists(path):
        raise SystemExit(f"找不到题库：{path}（用 --questions 指定）")
    return json.load(open(path, encoding="utf-8"))


def _pair_dir(head, arm):
    return f"{arm}·{head}"


_QSCACHE = {}


def _load_qs(scenario):
    if scenario in _QSCACHE:
        return _QSCACHE[scenario]
    p = os.path.join(_HERE, _QFILE.get(scenario, "_missing_"))
    qs = json.load(open(p, encoding="utf-8"))["questions"] if os.path.exists(p) else []
    _QSCACHE[scenario] = qs
    return qs


def build_pass_matrix(runs, phase):
    """返回 (arms, scen, passmat, retell, stored_mask)。
    passmat[arm][qid] = [0/1 per repeat]；stored_mask[arm] = 是否来自持久化 qa_pass
    （True=与主表口径一致，含 judge 复核；False=确定性回退，偏严）。"""
    arms, passmat, retell, scen, stored_mask = [], {}, {}, {}, {}
    head = {"p1": "p1", "p3": "p3"}.get(phase, phase)
    for path in runs:
        d = json.load(open(path, encoding="utf-8"))
        s = d.get("scenario", "?")
        qadir = d.get("qa_pass", {}) or {}
        for k, ans_by_id in (d.get("answers", {}) or {}).items():
            if not k.endswith(f"·{head}"):
                continue
            arm = k[: -(len(head) + 1)]
            if arm not in passmat:
                arms.append(arm)
                passmat[arm] = {}
                scen[arm] = s
                stored_mask[arm] = False
            stored = qadir.get(k, {})
            for q in _load_qs(s):
                qid = q["id"]
                if qid in stored:
                    passmat[arm][qid] = list(stored[qid])
                    stored_mask[arm] = True
                elif qid in ans_by_id:
                    spec = (q.get(f"check_{phase}") or q.get("check_after")
                            or q.get("check_before") or {})
                    passmat[arm][qid] = [1 if check_spec(a, spec)[0] else 0
                                         for a in ans_by_id[qid]]
        for arm, rt in (d.get("retell", {}) or {}).items():
            if arm in passmat:
                retell[arm] = {"coverage": rt.get("per_repeat_coverage", []) or [],
                               "rot": rt.get("per_repeat_rot", []) or []}
    return arms, scen, passmat, retell, stored_mask


def _mcnemar(a, b):
    """a,b = 等长 0/1 列表（已配对）。返回 (b01, c10, p)。"""
    a, b = np.asarray(a), np.asarray(b)
    b01 = int(np.sum((a == 1) & (b == 0)))
    c10 = int(np.sum((a == 0) & (b == 1)))
    n = b01 + c10
    if n == 0:
        return b01, c10, 1.0
    p = stats.binomtest(min(b01, c10), n, 0.5).pvalue * 2.0
    return b01, c10, min(1.0, p)


def _wilcoxon(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or np.allclose(x, y):
        return 1.0, 0.0
    try:
        r = stats.wilcoxon(x, y, zero_method="wilcox", alternative="two-sided")
        return float(r.pvalue), float(np.mean(x) - np.mean(y))
    except ValueError:
        return 1.0, float(np.mean(x) - np.mean(y))


def _layers(qs):
    out = {}
    for q in qs:
        out.setdefault(q.get("layer", "?"), []).append(q["id"])
    return out


def default_pairs(arms):
    pairs, structs = [], ["tree_v2", "full", "flat", "raptor"]
    for s in structs:
        ia, r = f"inc_{s}", f"reb_{s}"
        if ia in arms and r in arms:
            pairs.append((ia, r))
    for s in ("full", "flat", "raptor"):
        if "inc_tree_v2" in arms and f"inc_{s}" in arms:
            pairs.append(("inc_tree_v2", f"inc_{s}"))
    for s in ("full", "flat", "raptor"):
        if "tree_v2" in arms and s in arms:
            pairs.append(("tree_v2", s))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help="results.json（可重复）")
    ap.add_argument("--phase", default="p3", choices=["p1", "p3"])
    ap.add_argument("--questions", default="")
    ap.add_argument("--pairs", default="", help="A:B,C:D（默认自动）")
    ap.add_argument("--out", default="significance.csv")
    args = ap.parse_args()

    arms, scen, passmat, retell, stored_mask = build_pass_matrix(args.run, args.phase)
    if not arms:
        raise SystemExit("未在 answers 里找到任何臂")
    s0 = scen[arms[0]]
    qs = _load_questions(s0, args.questions)["questions"]
    lay = _layers(qs)
    qids = [q["id"] for q in qs]

    pairs = ([tuple(x.split(":")) for x in args.pairs.split(",") if x]
             if args.pairs else default_pairs(arms))

    rows = []
    nb_stored = sum(1 for a in arms if stored_mask.get(a))
    print(f"[scenario {s0}] phase={args.phase} arms={arms}")
    print(f"  口径: {nb_stored}/{len(arms)} 臂来自持久化 qa_pass（含 judge 复核）；"
          f"其余为确定性回退（偏严，会低估 tree）")
    print(f"  分层: " + "  ".join(f"{k}={len(v)}题" for k, v in lay.items()))
    for A, B in pairs:
        if A not in passmat or B not in passmat:
            print(f"  [skip] {A} vs {B}（缺臂）")
            continue
        common = [q for q in qids if q in passmat[A] and q in passmat[B]]
        a_flat = [x for q in common for x in passmat[A][q]]
        b_flat = [x for q in common for x in passmat[B][q]]
        b01, c10, p = _mcnemar(a_flat, b_flat)
        ra, rb = np.mean(a_flat), np.mean(b_flat)
        rows.append({"pair": f"{A} vs {B}", "metric": "qa_pass", "phase": args.phase,
                     "n_units": len(a_flat), "A": A, "B": B,
                     "rate_A": round(float(ra), 4), "rate_B": round(float(rb), 4),
                     "delta": round(float(ra - rb), 4), "test": "McNemar(exact)",
                     "stat": f"b01={b01},c10={c10}", "p_value": round(p, 5)})
        pa = [np.mean(passmat[A][q]) for q in common]
        pb = [np.mean(passmat[B][q]) for q in common]
        pw, dq = _wilcoxon(pa, pb)
        rows.append({"pair": f"{A} vs {B}", "metric": "qa_per_question_rate",
                     "phase": args.phase, "n_units": len(common), "A": A, "B": B,
                     "rate_A": round(float(np.mean(pa)), 4), "rate_B": round(float(np.mean(pb)), 4),
                     "delta": round(float(dq), 4), "test": "Wilcoxon(paired,q)",
                     "stat": "", "p_value": round(pw, 5)})
        for m in ("coverage", "rot"):
            xa, xb = retell.get(A, {}).get(m, []), retell.get(B, {}).get(m, [])
            n = min(len(xa), len(xb))
            if n >= 2:
                pw, dd = _wilcoxon(xa[:n], xb[:n])
                rows.append({"pair": f"{A} vs {B}", "metric": f"retell_{m}", "phase": args.phase,
                             "n_units": n, "A": A, "B": B,
                             "rate_A": round(float(np.mean(xa[:n])), 4),
                             "rate_B": round(float(np.mean(xb[:n])), 4),
                             "delta": round(float(dd), 4), "test": "Wilcoxon(paired,repeat)",
                             "stat": "", "p_value": round(pw, 5)})
        print(f"  {A:14s} vs {B:14s}  QA {ra:.3f} vs {rb:.3f}  Δ{ra-rb:+.3f}  McNemar p={p:.4f} "
              f"(b01={b01},c10={c10})")

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pair", "metric", "phase", "n_units", "A", "B",
                                          "rate_A", "rate_B", "delta", "test", "stat", "p_value"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n[done] {len(rows)} 行 → {args.out}")


if __name__ == "__main__":
    main()
