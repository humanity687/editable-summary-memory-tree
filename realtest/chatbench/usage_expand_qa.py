#!/usr/bin/env python3
"""usage_expand_qa.py — Phase C 第二组判别实验：ahi-bench qa 答案（3 树 × 51 题）。

与 usage_expand_bench.py 同协议（留一 + 对照），语料换成 ahi-bench 冷轮的 QA 答案
（clean 渲染无 #id，证据 = 逐字引用 `（依据: "摘录"）`；引用→原子用规范化子串匹配）。

用法：
  python3 realtest/chatbench/usage_expand_qa.py
"""
import argparse
import json
import os
import random
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for _p in (_ROOT, _REALTEST):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from realtest.usage import (split_units, classify_unit, screen_bigrams,
                            derive_unit_pairs, intersection_bring)
from realtest.textutil import norm_strict as norm
from realtest.chatbench.usage_replay import build_engine
from realtest.chatbench.usage_expand_bench import (merge_pairs, alive_pool,
                                                   coverage_bring)

QA_DIR = os.path.join(_ROOT, "ahi-bench", "out", "deepseek-v4-flash", "qa")
TREES = os.path.join(_ROOT, "ahi-bench", "out", "deepseek-v4-flash", "trees")
ANALYSIS_Q = ("根据你的记忆流，写一份关于你自己和另外两个 agent 的分析："
              "① 你们各自的立场与说话风格；② 你们之间的关系、共同点与分歧；"
              "③ 各举一个具体事件作为证据（附逐字摘录）。")
QUOTE_RE = re.compile(r'依据[:：]\s*["「]([^"」]{2,60})["」]')
SEC_RE = re.compile(r"^=== (bench-[abc])/recall \[(?:(\d+)/\d+|分析题)\]\s*(.*)$", re.M)


def load_facts():
    facts = []
    path = os.path.join(_ROOT, "realtest", "seed2", "facts.md")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\| F\d+ \| (.+?) \| (.+?) \|", line)
            if m:
                facts.append(m.group(1))
    return facts


def parse_sections():
    txt = open(os.path.join(QA_DIR, "answers_recall.txt"), encoding="utf-8").read()
    facts = load_facts()
    out = []
    for m in SEC_RE.finditer(txt):
        bench, idx, head_q = m.group(1), m.group(2), m.group(3).strip()
        start = m.end()
        nxt = SEC_RE.search(txt, start)
        body = txt[start:nxt.start()] if nxt else txt[start:]
        q = head_q if idx else ANALYSIS_Q
        if idx:
            i = int(idx) - 1
            if i < len(facts):
                q = facts[i]
        out.append({"bench": bench, "q": q, "answer": body.strip()})
    return out


class QuoteMapper:
    """引用摘录 → 原子 id（规范化子串；树内原子文本为真源）。"""

    def __init__(self, eng):
        self.eng = eng
        self.pool = [(norm(a.value), i) for i, a in eng.atoms.items()
                     if a.alive and a.value]

    def map_quote(self, quote):
        qn = norm(quote)
        if len(qn) < 2:
            return []
        return [i for t, i in self.pool if qn in t]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "usage_expand"))
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--rand-trials", type=int, default=50)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows = []
    rng = random.Random(42)
    for bench in ("bench-a", "bench-b", "bench-c"):
        secs = [s for s in parse_sections() if s["bench"] == bench]
        tr = os.path.join(TREES, f"{bench}_tree_final.json.gz")
        if not secs or not os.path.exists(tr):
            print(f"[qa-expand] {bench}: 跳过（无答案/树）", flush=True)
            continue
        print(f"[qa-expand] {bench}: {len(secs)} 答案，载入树 ...", flush=True)
        eng = build_engine(tr)
        mapper = QuoteMapper(eng)
        # 第一遍：逐题检索（锚/屏上/额外）+ 每答案对表（theta 0.58 与 1.1 两套）
        items = []
        for s in secs:
            q, ans = s["q"], s["answer"]
            hits = eng.retrieve(q, budget=11)
            screen_ids = {item for _k, item, _v in eng._emit()} | set(hits) \
                | set(eng.expand_leaves(hits))
            pos = {item: idx for idx, item in enumerate(eng.chain_ids())}
            scr_bg = screen_bigrams(eng, screen_ids)
            ev = sorted({i for qt in QUOTE_RE.findall(ans) for i in mapper.map_quote(qt)})
            pair_sets = {}
            for tag, theta in (("n", 0.58), ("r", 1.1)):
                pairs = {}
                for unit in split_units(ans):
                    quotes = QUOTE_RE.findall(unit)
                    cites = []
                    for qt in quotes:
                        cites.extend(mapper.map_quote(qt))
                    cites = list(dict.fromkeys(cites))
                    if cites:
                        row = {"cites": cites, "matches": []}
                    else:
                        row, _st = classify_unit(eng, unit, screen_ids=screen_ids,
                                                 scr_bg=scr_bg, cited_ids=set(),
                                                 pos=pos)
                        row = {"cites": row.get("cites") or [],
                               "matches": row.get("matches") or []}
                    for a, b, sim, tiers in derive_unit_pairs(eng, row, theta):
                        key = (a, b) if a <= b else (b, a)
                        d = pairs.setdefault(key, {"weight": 0.0, "uses": 0})
                        d["weight"] += 1.0
                        d["uses"] += 1
                pair_sets[tag] = pairs
            items.append({"q": q, "hits": hits[:6], "extra": hits[6:11],
                          "screen": screen_ids, "evidence": ev,
                          "n_pairs": pair_sets["n"], "r_pairs": pair_sets["r"]})
        pool = alive_pool(eng)
        n_pairs_total = sum(len(it["n_pairs"]) for it in items)
        print(f"  答案 {len(items)}；非冗余对 {n_pairs_total}；"
              f"证据（引用映射）{sum(len(it['evidence']) for it in items)}", flush=True)
        # 第二遍：留一评估
        for j, it in enumerate(items):
            anchors = it["hits"]
            anchor_set = set(anchors)
            merged_n = merge_pairs([x["n_pairs"] for k, x in enumerate(items) if k != j])
            merged_r = merge_pairs([x["r_pairs"] for k, x in enumerate(items) if k != j])
            ev = set(it["evidence"])
            usage = intersection_bring(anchors, merged_n, topk=args.topk)
            usage_cov = coverage_bring(eng, anchor_set, merged_n, topk=args.topk)
            redund = intersection_bring(anchors, merged_r, topk=args.topk)
            eps_all = {x for key in merged_n for x in key}
            rand_hits = []
            for _ in range(args.rand_trials):
                picks = rng.sample(pool, min(args.topk, len(pool)))
                rand_hits.append(len(set(picks) & ev))
            rows.append({
                "bench": bench, "q": it["q"][:40],
                "n_evidence": len(ev),
                "direct_hits": len(anchor_set & ev),
                "usage_hits": len(set(usage) & ev),
                "usage_new": len(set(usage) & (ev - anchor_set)),
                "usage_n": len(usage),
                "usage_cov_hits": len(set(usage_cov) & ev),
                "usage_cov_new": len(set(usage_cov) & (ev - anchor_set)),
                "usage_cov_n": len(usage_cov),
                "extra_hits": len(set(it["extra"]) & ev),
                "extra_new": len(set(it["extra"]) & (ev - anchor_set)),
                "redund_hits": len(set(redund) & ev),
                "redund_new": len(set(redund) & (ev - anchor_set)),
                "reach_evidence": len(ev & eps_all),
                "rand_hits_mean": round(sum(rand_hits) / len(rand_hits), 3) if rand_hits else 0.0,
            })
        print(f"  {bench} 完成留一评估", flush=True)

    def agg(key):
        return sum(r[key] for r in rows)

    summary = {
        "n_questions": len(rows),
        "evidence_total": agg("n_evidence"),
        "direct_hits": agg("direct_hits"),
        "usage_hits": agg("usage_hits"), "usage_new": agg("usage_new"),
        "usage_cov_hits": agg("usage_cov_hits"), "usage_cov_new": agg("usage_cov_new"),
        "extra_hits": agg("extra_hits"), "extra_new": agg("extra_new"),
        "redund_hits": agg("redund_hits"), "redund_new": agg("redund_new"),
        "reach_evidence": agg("reach_evidence"),
        "rand_hits_mean_sum": round(sum(r["rand_hits_mean"] for r in rows), 3),
        "usage_win_vs_extra": sum(1 for r in rows if r["usage_new"] > r["extra_new"]),
        "usage_win_vs_redund": sum(1 for r in rows if r["usage_new"] > r["redund_new"]),
        "usage_cov_win_vs_extra": sum(1 for r in rows
                                      if r["usage_cov_new"] > r["extra_new"]),
        "per_question": rows,
    }
    with open(os.path.join(args.out, "usage_expand_qa_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("\n[qa-expand] 汇总：")
    print(f"  证据总数 {summary['evidence_total']}；锚命中 {summary['direct_hits']}")
    print(f"  交集带动：命中 {summary['usage_hits']}（新增 {summary['usage_new']}）")
    print(f"  覆盖感知：命中 {summary['usage_cov_hits']}（新增 {summary['usage_cov_new']}）")
    print(f"  相似度延伸：命中 {summary['extra_hits']}（新增 {summary['extra_new']}）")
    print(f"  冗余边：命中 {summary['redund_hits']}（新增 {summary['redund_new']}）")
    print(f"  可达性上界：{summary['reach_evidence']}/{summary['evidence_total']}")
    print(f"  随机对照：期望 {summary['rand_hits_mean_sum']}")
    print(f"  胜场：vs 相似延伸 {summary['usage_win_vs_extra']}/{len(rows)}，"
          f"vs 冗余边 {summary['usage_win_vs_redund']}/{len(rows)}，"
          f"覆盖感知 vs 相似延伸 {summary['usage_cov_win_vs_extra']}/{len(rows)}")
    print(f"\n[qa-expand] 报告 → {os.path.join(args.out, 'usage_expand_qa_result.json')}")


if __name__ == "__main__":
    main()
