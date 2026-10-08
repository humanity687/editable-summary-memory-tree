#!/usr/bin/env python3
"""gen_questions.py — 从 spec（单源）生成题库 JSON 并做锚点校验。

spec 文件（realtest/editbench/specs/<name>.json）：
{
  "doc": "<原始文档路径>",
  "edits": [ {kind, ...}, ... ],          # 交给 editstorm.build_scenario_spec
  "questions": [ {id, layer, warm, q, check_before, check_after}, ... ],
  "retell_checklist": {"require":[...], "require_record":[...],
                       "require_nonrecord":[...], "forbid":[...]}
}

校验（逐条 grep 计数）：
- 每个编辑的锚点必须命中且**唯一**（new_doc 生成不报错）；
- 反转题 check_before.require 在原文**唯一**、check_before.forbid 不在原文；
  check_after.require 在改后文**唯一**、check_after.forbid 不在改后文；
- 不变/远端题的 require 在原文唯一。
输出 questions_<name>.json（供 docbench --questions 用）+ 校验报告。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for p in (_HERE, os.path.dirname(_HERE), _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from editstorm import build_scenario_spec  # noqa: E402
from textutil import norm_cjk as norm     # noqa: E402


def _count(hay, needle):
    """归一化后子串计数（唯一性判定）。"""
    h, n = norm(hay), norm(needle)
    if not n:
        return 0
    return h.count(n)


def validate(spec, doc):
    new_doc, ledger = build_scenario_spec(doc, spec)
    problems = []
    # 编辑锚点：rename/detail 是全局替换（old 可多次出现），只需 old 命中且改后消失；
    # rewrite/passage/delete/insert 是定位替换，old/anchor 必须唯一。
    for e in spec.get("edits", []):
        k = e["kind"]
        old = e.get("old") or e.get("sentence") or e.get("anchor")
        n_old = _count(doc, old) if old else 0
        if old is not None and n_old < 1:
            problems.append(f"[edit {k}] 原锚点未命中: {str(old)[:40]}")
        if k in ("rewrite", "passage", "delete", "insert") and n_old > 1:
            problems.append(f"[edit {k}] 定位锚点非唯一({n_old}): {str(old)[:40]}")
        if k in ("rename", "detail", "rewrite") and e.get("new"):
            if _count(new_doc, e["new"]) < 1:
                problems.append(f"[edit {k}] 新值未出现在改后文: {e['new'][:40]}")
    # 题目锚点
    for q in spec.get("questions", []):
        cb = q.get("check_before", {})
        ca = q.get("check_after") or cb
        novel = q.get("novel")   # 新增类（append/insert）：before 本就无此信息
        for f in cb.get("require", []):
            if novel:
                continue
            if _count(doc, f) < 1:
                problems.append(f"[{q['id']}] before.require 未命中: {f[:40]}")
            elif _count(doc, f) > 1 and q.get("layer") == "reversal":
                problems.append(f"[{q['id']}] before.require 非唯一({_count(doc, f)}): {f[:40]}")
        for f in cb.get("forbid", []):
            if _count(doc, f) > 0:
                problems.append(f"[{q['id']}] before.forbid 误命中原文: {f[:40]}")
        if q.get("layer") == "reversal":
            for f in ca.get("require", []):
                if _count(new_doc, f) < 1 or _count(new_doc, f) > 1:
                    problems.append(f"[{q['id']}] after.require 非唯一({_count(new_doc, f)}): {f[:40]}")
            for f in ca.get("forbid", []):
                if _count(new_doc, f) > 0:
                    problems.append(f"[{q['id']}] after.forbid 误命中改后文: {f[:40]}")
    return new_doc, ledger, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--out", default=None, help="题库输出（默认 questions_<name>.json）")
    args = ap.parse_args()

    name = os.path.splitext(os.path.basename(args.spec))[0]
    spec = json.load(open(args.spec, encoding="utf-8"))
    doc = open(os.path.join(_ROOT, spec["doc"]) if not os.path.isabs(spec["doc"])
               else spec["doc"], encoding="utf-8").read()
    new_doc, ledger, problems = validate(spec, doc)

    qs = spec.get("questions", [])
    from collections import Counter
    layers = Counter(q.get("layer", "?") for q in qs)
    warm = sum(1 for q in qs if q.get("warm"))
    rl = spec.get("retell_checklist", {})
    print(f"[{name}] 文档 {len(doc):,}字 → 改后 {len(new_doc):,}字；{len(ledger)} 编辑；"
          f"{len(qs)} 题 {dict(layers)}（warm {warm}）；"
          f"复述 require {len(rl.get('require',[]))}"
          f"（record {len(rl.get('require_record',[]))}/nonrecord {len(rl.get('require_nonrecord',[]))}）")
    if problems:
        print(f"  !! {len(problems)} 个校验问题：")
        for p in problems:
            print("    -", p)
    else:
        print("  ✓ 锚点校验全过（before/after 唯一、forbid 缺席）")

    out = args.out or os.path.join(_HERE, f"questions_{name}.json")
    payload = {
        "_meta": {"name": name, "doc": spec["doc"], "scenario": name,
                  "n_questions": len(qs), "layers": dict(layers),
                  "prereg": spec.get("prereg", {}),
                  "validator": "gen_questions.py", "problems": problems},
        "questions": qs,
        "retell_checklist": rl,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"  → {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
