#!/usr/bin/env python3
"""bonus.py — 编辑效果 bonus 分析（论文的"额外发现"章节）。

两类指标：
  A. 摘要旧内容残留：编辑后，树的**存活摘要节点**是否仍含被替换的旧值（应=0）；
     同时看存活原子里的新旧值（编辑路径正确性）与全局视图（无 query 组装）的泄漏。
  B. 重写段连贯性：整段改写后，新段与**其上下文**是否自洽；
     自动侧 = 新文档里是否仍残留与改后结论矛盾的旧断言（编辑不彻底的证据）；
     LLM 侧（--judge）= 把新段 ± 上下文给 judge，问是否连贯/有无矛盾。

用法：
  python3 realtest/editbench/bonus.py \
      --tree realtest/editbench/out/ahi_tree_trial_r20/trees/tree_v2·inc_final.json.gz \
      --scenario ahi --cooldown --judge \
      --doc realtest/editbench/ahi_script.txt --out realtest/editbench/out/bonus_inc
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for p in (_ROOT, _REALTEST, os.path.join(_REALTEST, "chatbench"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from llmclient import make_client                       # noqa: E402
from realengine import RealEngine, engine_kwargs        # noqa: E402
from editstorm import build_scenario, build_scenario_ahi  # noqa: E402
import judge as judge_mod                               # noqa: E402

SCENARIOS = {"seed2": build_scenario, "ahi": build_scenario_ahi}


class Dummy:
    model = "dummy"

    def chat(self, s, u):
        return "", {"prompt_tokens": 0, "completion_tokens": 0}, 0.0


def count_old(text, old):
    """旧串计数；单字名（如"墨"）用负向断言排除新名（"墨某"）。"""
    if old and len(old) == 1:
        return len(re.findall(re.escape(old) + r"(?!" + re.escape("某") + ")", text or ""))
    return (text or "").count(old) if old else 0


def _final_form(text, ledger, start):
    """把 text 看作第 start 步之前的内容，施加其后的所有确定性编辑，得到它在终稿中的形态。
    用于在终稿中定位被后续编辑（如 rename）改写过的段落。"""
    for e in ledger[start:]:
        old, new = e.get("old") or "", e.get("new")
        if e["kind"] in ("rename", "detail", "rewrite", "passage") and old:
            if new is not None:
                text = text.replace(old, new)
        elif e["kind"] == "delete" and old:
            text = text.replace(old, "")
    return text


def analyze_tree(eng, forms):
    alive_nodes = [n for n in eng.nodes.values() if n.alive]
    alive_atoms = [a for a in eng.atoms.values() if a.alive]
    rows = []
    for f in forms:
        old, new = f["old"], f["new"]
        node_old = sum(1 for n in alive_nodes if count_old(n.value or "", old))
        node_new = sum(1 for n in alive_nodes if new and new in (n.value or ""))
        atom_old = sum(1 for a in alive_atoms if count_old(a.value or "", old))
        atom_new = sum(1 for a in alive_atoms if new and new in (a.value or ""))
        rows.append({"kind": f["kind"], "old": old[:30], "new": new[:30],
                     "node_old": node_old, "node_new": node_new,
                     "atom_old": atom_old, "atom_new": atom_new})
    return rows


def global_view(eng, budget):
    em = eng.assemble(None, budget=budget, allow_merge=False)
    return eng.render_emission(em)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--scenario", default="ahi", choices=list(SCENARIOS))
    ap.add_argument("--doc", default=os.path.join(_HERE, "ahi_script.txt"))
    ap.add_argument("--tree", required=True)
    ap.add_argument("--cooldown", action="store_true",
                    help="先施加冷却窗口（tick45+merge; tick30+merge）再分析")
    ap.add_argument("--budget", type=int, default=24000)
    ap.add_argument("--retr-budget", type=int, default=20)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "bonus"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = "deepseek"
    cfg["deepseek"]["model"] = args.model
    cfg["deepseek"]["temperature"] = 0.0

    from tree_io import load_tree
    eng = RealEngine(Dummy(), **engine_kwargs(cfg, args.budget, args.retr_budget))
    load_tree(eng, args.tree)
    if args.cooldown:
        # 最小冷却（与 arms.TreeArm.settle 同源）：只解占位，不整树压平。
        # 旧版 tick(45)+tick(30) 会把整树 HP 打到 0 → merge 把全文压成少数根摘要。
        from arms import _COOL_TICKS
        eng.tick(1)
        sig = eng.merge_signal()
        if sig == "force":
            eng.merge_pass(force_out=True)
        elif sig == "regular":
            eng.merge_pass()
        eng.tick(_COOL_TICKS)
        eng.merge_pass()

    doc = open(args.doc, encoding="utf-8").read().strip()
    new_doc, ledger = SCENARIOS[args.scenario](doc)
    forms = [{"kind": e["kind"], "old": e["old"], "new": e["new"]} for e in ledger]

    report = {"tree": args.tree, "cooldown": args.cooldown,
              "alive_nodes": sum(1 for n in eng.nodes.values() if n.alive),
              "alive_atoms": sum(1 for a in eng.atoms.values() if a.alive),
              "tiling": len(eng.tiling)}

    print("=== A. 摘要旧内容残留（编辑后存活摘要节点应 0 旧 / 有新）===")
    rows = analyze_tree(eng, forms)
    report["summary_residual"] = rows
    for r in rows:
        flag = "OK" if r["node_old"] == 0 else "**残留**"
        print(f"  [{r['kind']:7s}] 旧={r['old']!r} 新={r['new']!r} | "
              f"节点 旧{r['node_old']}/新{r['node_new']} 原子 旧{r['atom_old']}/新{r['atom_new']} {flag}")

    # 全局视图（无 query）泄漏
    gv = global_view(eng, args.budget)
    leak = {f["old"][:20]: count_old(gv, f["old"]) for f in forms if f["old"]}
    report["global_view_leak"] = leak
    print(f"\n全局视图（无 query，{len(gv)} 字）旧值泄漏：{leak}")

    # B. 重写段连贯性（自动侧：新文档里矛盾的旧断言残留）
    print("\n=== B. 重写段连贯性（自动：新文档残留的矛盾旧断言）===")
    coh = []
    for i, e in enumerate(ledger):
        if e["kind"] not in ("passage", "rewrite"):
            continue
        residual = new_doc.count(e["old"]) if e["old"] else 0
        # 该段在**终稿**中的形态：把其后编辑的确定性替换继续施加到它身上（处理叠加编辑）
        final_new = _final_form(e["new"] or "", ledger, i + 1)
        item = {"kind": e["kind"], "old": e["old"][:40], "new": (e["new"] or "")[:40],
                "new_final": final_new, "residual_in_new_doc": residual}
        idx = new_doc.find(final_new) if final_new else -1
        if idx >= 0:
            item["context_before"] = new_doc[max(0, idx - 200):idx]
            item["context_after"] = new_doc[idx + len(final_new): idx + len(final_new) + 200]
            item["located"] = True
        else:
            item["located"] = False
        coh.append(item)
        print(f"  [{e['kind']}] 改后旧断言在新文档仍出现 {residual} 次；"
              f"段落在终稿定位 {'成功' if idx >= 0 else '失败'}")
    report["coherence_auto"] = coh

    if args.judge:
        jcfg = json.loads(json.dumps(cfg))
        jllm = make_client(jcfg)
        print("\n=== B2. 连贯性 LLM judge（新段 vs 上下文）===")
        for item in coh:
            prompt = ("下面是一份文档中某一段被改写后的【新段落】及其【上下文】。"
                      "判断：新段落与上下文是否连贯、有无事实矛盾？\n"
                      f"【上文】{item.get('context_before','')}\n"
                      f"【新段落】{item.get('new_final') or item['new']}\n"
                      f"【下文】{item.get('context_after','')}\n\n"
                      '输出 JSON：{"coherent": "是|否", "reason": "一句话"}')
            text, _u, _l = jllm.chat(judge_mod.JUDGE_SYSTEM, prompt)
            d = judge_mod._parse_json(text)
            item["judge_coherent"] = d.get("coherent", d.get("field"))
            item["judge_reason"] = d.get("reason", text[:120])
            print(f"  [{item['kind']}] coherent={item.get('judge_coherent')} "
                  f"reason={item.get('judge_reason')}")
    report["coherence_judge"] = [c for c in report["coherence_auto"] if "judge_coherent" in c]

    with open(os.path.join(args.out, "bonus.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[done] → {args.out}/bonus.json")


if __name__ == "__main__":
    main()
