#!/usr/bin/env python3
"""cache_probe.py — 测各结构在连续问答下的 **KV 缓存前缀命中率**（验证成本排序假设）。

假设：DeepSeek 上下文缓存按请求**前缀**命中；full 的文档前缀跨问题稳定 → 命中高；
flat/raptor 按 query 排序检索、tree 每问重组装+影子累积 → 前缀不稳定 → 命中低。
本探针不谈正确性，只对一个文档建基线臂，按题库顺序提问，记录每次 prompt_cache_hit/miss。

用法：
  python3 realtest/editbench/cache_probe.py --doc realtest/seed2/story.txt \
      --questions realtest/editbench/questions_full.json --limit 12
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

from llmclient import make_client          # noqa: E402
from realengine import _load_encoder       # noqa: E402
from arms import STRUCTURES, make_arm      # noqa: E402
from docbench import SYSTEM, _ask          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--doc", default=os.path.join(_REALTEST, "seed2", "story.txt"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "questions_full.json"))
    ap.add_argument("--keys", default=os.path.join(_HERE, "keys.local.json"))
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--tree-budget", type=int, default=0, help="tree_v2 专用预算；0=同 --budget")
    ap.add_argument("--structures", default="full,flat,raptor,tree_v2")
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "cache_probe"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["provider"] = "deepseek"
    cfg["deepseek"]["temperature"] = 0.0
    base = make_client(cfg)
    budget = int(cfg.get("assembly_budget", 16000))
    retr_budget = int(cfg.get("retrieval_budget", 6))
    keymap = json.load(open(args.keys, encoding="utf-8")) if os.path.exists(args.keys) else {}

    def client_for(tag):
        if tag not in keymap:
            return base
        c = json.loads(json.dumps(cfg))
        c["deepseek"]["api_key"] = keymap[tag]
        return make_client(c)

    doc = open(args.doc, encoding="utf-8").read().strip()
    questions = json.load(open(args.questions, encoding="utf-8"))["questions"][:args.limit]
    enc = _load_encoder()
    structs = [s for s in args.structures.split(",") if s in STRUCTURES]

    def budget_for(name):
        if name == "tree_v2" and args.tree_budget:
            return int(args.tree_budget)
        return budget

    report = {}
    for name in structs:
        arm = make_arm(name, client_for(name), client_for(name), cfg, budget_for(name),
                       retr_budget, enc, doc, tag=name)
        calls = []
        for item in questions:
            arm.reset()
            ctx = arm.context(item["q"])
            _t, usage, _l = _ask(arm.llm, ctx, item["q"])
            calls.append({"q": item["id"], "prompt": usage.get("prompt_tokens", 0),
                          "hit": usage.get("cache_hit_tokens", 0),
                          "miss": usage.get("cache_miss_tokens", 0)})
        st = arm.llm.stats()
        report[name] = {"stats": st, "calls": calls}
        print(f"  {name:8s} hit_rate={st['cache_hit_rate']} "
              f"hit={st['cache_hit_tokens']} miss={st['cache_miss_tokens']} "
              f"prompt={st['prompt_tokens']}", flush=True)
        for c in calls:
            print(f"     {c['q']:16s} prompt={c['prompt']:7d} hit={c['hit']:7d} miss={c['miss']:7d}")

    with open(os.path.join(args.out, "cache_probe.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[done] → {args.out}/cache_probe.json")


if __name__ == "__main__":
    main()
