#!/usr/bin/env python3
"""appendixA.py — 附录 A：分辨率遗忘曲线（真实引擎，A0/A1/A2）。

装置（沿用 memcurve）：t=0 注入 N 条带独有 token 的事实；随后持续读入 distractor
（ahi-act 剧本句）并步进老化（insert + tick + 条件 merge）。时间 = 累计处理字符 / 轮数。

三臂（各自独立实例，共享同一构建协议与 distractor 序列）：
  A0 闲置    构建后不检索、不编辑，仅步进（= 系统在别处运行、时间流逝）。
  A1 周期访问 同 A0，但每 `access_every` 轮对**固定随机 20%** 事实检索一次（写回续命）。
  A2 恢复    同 A0 闲置至 R_default ≤ `recover_below`（默认 0.18），之后每轮对全部事实
             检索（写回续命）——测"闲置压缩后按需恢复，记忆率何时提起来"。

每采样点测：
  结构量：铺陈项数 / 平均树深 / 最大树深 / 默认视野原子可见占比（视野原子/存活原子）/ 视野字符数
  记忆率：默认视图事实保留率 R_default（铺陈视图里 token 仍可见）；数据可达率 R_reach
          （事实原子未墓碑 = 数据未丢，仅分辨率粗化；不检索、不扰动引擎）。

用法：
  python3 realtest/memcurve/appendixA.py --mock --n-facts 60 --budget 8000 --rounds 64
  python3 realtest/memcurve/appendixA.py --n-facts 60 --budget 8000 --rounds 64 \
      --plot --out realtest/memcurve/out/appendixA
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"),
          os.path.join(_REALTEST, "editbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from memcurve import (TreeArm, load_facts, synth_facts, load_distractors,   # noqa: E402
                      make_llm, _contains)


class Arm:
    """A0/A1/A2 统一外壳：A0=off，A1=periodic，A2=recover。均先按 idle 步进。"""

    def __init__(self, name, cfg, llm, facts, budget, retr, seed=42,
                 mode="idle", access_every=4, access_frac=0.2, recover_below=0.18):
        self.name = name
        self.facts = facts
        self.budget = budget
        self.retr = retr
        self.mode = mode
        self.t = TreeArm(cfg, llm, facts, budget=budget, retr=retr, recall_mode="off")
        ids = [f["id"] for f in facts]
        rng = random.Random(seed)
        k = max(1, int(round(len(ids) * access_frac)))
        self.access_ids = set(rng.sample(ids, k))
        self.access_facts = [f for f in facts if f["id"] in self.access_ids]
        self.access_every = access_every
        self.recover_below = recover_below
        self.round = 0
        self.shadows = []          # 最近一次检索命中（写回已在 retrieve 内完成）
        self.recall_on = False

    def _retrieve(self, subset):
        sh, seen = [], set()
        for f in subset:
            for h in self.t.eng.retrieve(f["text"], budget=self.retr):
                if h not in seen:
                    seen.add(h)
                    sh.append(h)
        return sh

    def age(self, batch):
        self.t.age(batch)          # recall_mode=off → 仅 insert+tick+条件 merge
        self.round += 1
        if self.mode == "periodic" and self.round % self.access_every == 0:
            self.shadows = self._retrieve(self.access_facts)
        elif self.mode == "recover" and self.recall_on:
            self.shadows = self._retrieve(self.facts)


def structure_metrics(eng, budget):
    depths = [eng._depth(it) for it in eng.tiling if not eng._is_atom(it)]
    heights = eng._heights()
    return {
        "tiling": len(eng.tiling),
        "n_alive_atoms": sum(1 for a in eng.atoms.values() if a.alive),
        "n_alive_nodes": sum(1 for n in eng.nodes.values() if n.alive),
        "tree_height": max(heights.values()) if heights else 0,
        "served_depth_avg": round(sum(depths) / len(depths), 2) if depths else 0.0,
        "served_depth_max": max(depths) if depths else 0,
    }


def probe(arm):
    eng = arm.t.eng
    em = eng.assemble(None, budget=arm.budget, allow_merge=False)
    txt = eng.render_emission(em)
    n = len(arm.facts)
    em_ids = {it for _, it, _ in em}
    res = arm.t._resolve(em_ids)
    # R_reach = 数据可达性：事实原子未墓碑（不检索、不扰动引擎）＝"数据未删，只是分辨率粗化"。
    reach = sum(1 for f in arm.facts
                if arm.t._fid[f["id"]] in eng.atoms and eng.atoms[arm.t._fid[f["id"]]].alive)
    row = {
        "round": arm.round,
        "recall_on": arm.recall_on,
        "R_default": round(sum(1 for f in arm.facts if _contains(txt, f)) / n, 3),
        "R_reach": round(reach / n, 3),
        "atom_vis_facts": round(sum(1 for v in res.values() if v == "atom") / n, 3),
        "node_vis_facts": round(sum(1 for v in res.values() if v.startswith("node")) / n, 3),
        "gone_facts": round(sum(1 for v in res.values() if v == "gone") / n, 3),
        "view_chars": len(txt),
    }
    view_atoms = sum(1 for _, it, _ in em if eng._is_atom(it))
    row.update(structure_metrics(eng, arm.budget))
    row["atom_vis_ratio"] = round(view_atoms / max(1, row["n_alive_atoms"]), 3)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--facts", default=os.path.join(_HERE, "facts.json"))
    ap.add_argument("--distractors", default=os.path.join(_REALTEST, "editbench", "ahi_script.txt"))
    ap.add_argument("--budget", type=int, default=8000)
    ap.add_argument("--rounds", type=int, default=64)
    ap.add_argument("--per-round", type=int, default=8)
    ap.add_argument("--check-every", type=int, default=2)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--n-facts", type=int, default=60)
    ap.add_argument("--retr-budget", type=int, default=6)
    ap.add_argument("--access-every", type=int, default=4)
    ap.add_argument("--access-frac", type=float, default=0.2)
    ap.add_argument("--recover-below", type=float, default=0.18)
    ap.add_argument("--distill-parallel", type=int, default=1,
                    help="蒸馏并发（默认 1=确定性，使三臂干预前轨迹一致）")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "appendixA"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["distill_parallel"] = args.distill_parallel
    facts = load_facts(args.facts)
    if args.n_facts and args.n_facts > len(facts):
        facts = synth_facts(args.n_facts, facts)
    elif args.n_facts and args.n_facts < len(facts):
        facts = facts[:args.n_facts]
    distractors = load_distractors(args.distractors)
    llm = make_llm(cfg, args.mock)

    dtext = "\n".join(distractors)
    coll = [(f["id"], k) for f in facts for k in f["keys"] if k in dtext]
    if coll:
        print("[warn] fact token 与 distractor 碰撞:", coll)
    print(f"[appendixA] facts={len(facts)} distractors={len(distractors)} "
          f"budget={args.budget} rounds={args.rounds} mock={args.mock}")

    arms = [
        Arm("A0", cfg, llm, facts, args.budget, args.retr_budget, mode="idle"),
        Arm("A1", cfg, llm, facts, args.budget, args.retr_budget, mode="periodic",
            access_every=args.access_every, access_frac=args.access_frac),
        Arm("A2", cfg, llm, facts, args.budget, args.retr_budget, mode="recover",
            recover_below=args.recover_below),
    ]

    results = {"facts": [f["id"] for f in facts], "budget": args.budget,
               "rounds": args.rounds, "per_round": args.per_round, "mock": args.mock,
               "access_every": args.access_every, "access_frac": args.access_frac,
               "recover_below": args.recover_below, "checkpoints": []}

    def checkpoint():
        rec = {"x_chars": x, "round": arms[0].round, "arms": {}}
        for a in arms:
            rec["arms"][a.name] = probe(a)
        results["checkpoints"].append(rec)
        a2 = next((a for a in arms if a.mode == "recover"), None)
        if a2 is not None and not a2.recall_on and \
                rec["arms"][a2.name]["R_default"] <= args.recover_below:
            a2.recall_on = True
            print(f"[{a2.name}] 触发恢复 @ round={rec['round']} "
                  f"(R_default={rec['arms'][a2.name]['R_default']:.3f} ≤ {args.recover_below})",
                  flush=True)
        parts = [f"{a.name}: R={rec['arms'][a.name]['R_default']:.2f} "
                 f"atom={rec['arms'][a.name]['atom_vis_facts']:.2f} "
                 f"vis={rec['arms'][a.name]['atom_vis_ratio']:.2f} "
                 f"h={rec['arms'][a.name]['tree_height']}" for a in arms]
        print(f"[T={rec['round']:>2} x={x:6d}] " + " | ".join(parts), flush=True)

    x = 0
    checkpoint()
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
        for a in arms:
            a.age(batch)
        if (r + 1) % args.check_every == 0:
            checkpoint()

    with open(os.path.join(args.out, "appendixA.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

    _write_table(results, os.path.join(args.out, "table.csv"))
    if args.plot:
        _plot(results, os.path.join(args.out, "figA1_forgetting.png"))
    print(f"\n[done] → {args.out}/appendixA.json + table.csv"
          + (" + figA1_forgetting.png" if args.plot else ""))


_T_GRID = [0, 1, 2, 4, 8, 16, 32, 64]


def _nearest(cps, t):
    return min(cps, key=lambda c: (abs(c["round"] - t), c["round"] < t))


def _write_table(results, path):
    cps = results["checkpoints"]
    rows = []
    header = ("T,A0_atom_vis,A0_R_default,A0_R_reach,"
              "A1_atom_vis,A1_R_default,A1_R_reach,"
              "A2_atom_vis,A2_R_default,A2_R_reach,A2_recall_on")
    rows.append(header)
    for t in _T_GRID:
        c = _nearest(cps, t)
        a = c["arms"]
        rows.append(",".join(str(v) for v in [
            c["round"],
            a["A0"]["atom_vis_facts"], a["A0"]["R_default"], a["A0"]["R_reach"],
            a["A1"]["atom_vis_facts"], a["A1"]["R_default"], a["A1"]["R_reach"],
            a["A2"]["atom_vis_facts"], a["A2"]["R_default"], a["A2"]["R_reach"],
            int(a["A2"]["recall_on"]),
        ]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def _plot(results, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB",
                                           "Arial Unicode MS", "STHeiti", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print("[warn] matplotlib 不可用，跳过绘图:", e)
        return
    cps = results["checkpoints"]
    rounds = [c["round"] for c in cps]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    colors = {"A0": "#d62728", "A1": "#2ca02c", "A2": "#6a51a3"}
    labels = {"A0": "A0 闲置", "A1": "A1 周期访问(20%/4轮)", "A2": "A2 闲置后恢复"}
    for name in ("A0", "A1", "A2"):
        y = [c["arms"][name]["R_default"] for c in cps]
        ax1.plot(rounds, y, "o-", ms=3, color=colors[name], label=labels[name])
        y2 = [c["arms"][name]["atom_vis_facts"] for c in cps]
        ax2.plot(rounds, y2, "o-", ms=3, color=colors[name], label=labels[name])
    ax1.set_xlabel("T（轮）")
    ax1.set_ylabel("默认视图事实保留率 $R_{default}$")
    ax1.set_title("(a) 记忆率：默认视图事实保留率")
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=8)
    ax2.set_xlabel("T（轮）")
    ax2.set_ylabel("原子级可见事实占比")
    ax2.set_title("(b) 分辨率：原子级可见事实占比")
    ax2.set_ylim(-0.05, 1.05)
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=8)
    # A2 恢复触发竖线
    trig = next((c["round"] for c in cps if c["arms"]["A2"]["recall_on"]), None)
    if trig is not None:
        ax1.axvline(trig, color="#6a51a3", ls=":", lw=1.2)
        ax1.text(trig, 1.02, f" A2 开召回 T={trig}", color="#6a51a3", fontsize=8, va="top")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    for ext in ("pdf", "svg"):
        try:
            fig.savefig(path.rsplit(".", 1)[0] + "." + ext)
        except Exception:
            pass
    print(f"[plot] → {path}")


if __name__ == "__main__":
    main()
