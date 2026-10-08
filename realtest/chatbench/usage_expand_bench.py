#!/usr/bin/env python3
"""usage_expand_bench.py — Phase C 判别实验：交集带动（使用边）证据命中 vs 四组对照。

零 LLM 离线（冻结树 + 既有答案，USAGE_SPEC §7）。协议（留一法，防自证）：
  1. 对每个答案 i：UsageLayer(offline) 记录其非冗余 A↔B 对
     （屏上集 = 冻结树 tiling + 该答案 direct/hub，与 usage_replay 同口径）；
  2. 对目标问题 j：fresh 引擎 retrieve(q_j, budget=11) → 锚 = 前 6 名
     （每题独立加载，避免跨题 HP/promote 污染）；
  3. 候选 = intersection_bring(锚, 其余答案合并对, topk=5)（留一：不含 j 自身）；
  4. 证据链 = answer_j.cited_texts；度量 候选 ∩ 证据 与新增证据（∉ 锚）。
  对照：① 随机卡；② 相似度延伸（同一次检索的第 7–11 名，不靠边多取 5 张）；
        ③ 冗余边带动（θ=1.1 建对，只关非冗余过滤）；④ 历史 conn_weight hub（记录值）。

用法：
  python3 realtest/chatbench/usage_expand_bench.py \
      --run realtest/chatbench/out/ctx_on_u \
      --out realtest/chatbench/out/usage_expand
"""
import argparse
import json
import os
import random
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for _p in (_ROOT, _REALTEST):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from realtest.usage import (UsageLayer, split_units, intersection_bring,
                            is_ancestor)
from realtest.chatbench.usage_replay import build_engine, screen_sets


def covered_by_anchors(eng, anchor_set, item):
    """锚覆盖：item 恰为锚，或 item 是某锚（摘要节点）的后代。"""
    if item in anchor_set:
        return True
    return any(is_ancestor(eng, a, item) for a in anchor_set)


def coverage_bring(eng, anchor_set, pairs, topk=5):
    """覆盖感知交集带动：端点被锚（或其子树）覆盖 且 候选端点不被覆盖 → 候选。
    纯读；权重 = 边权累计。"""
    score = {}
    for (a, b), d in (pairs or {}).items():
        w = float(d.get("weight", 0.0))
        for x, y in ((a, b), (b, a)):
            if covered_by_anchors(eng, anchor_set, x) and \
               not covered_by_anchors(eng, anchor_set, y):
                score[y] = score.get(y, 0.0) + w
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return [i for i, _ in ranked[:topk]]


def answer_pairs(eng, answer, params=None):
    """单答案 → 非冗余对表（离线，不落盘不改树）。"""
    layer = UsageLayer(eng, out_dir=None, params=params)
    screen_ids, _missing = screen_sets(eng, answer)
    units = [(u, None) for u in split_units(answer.get("answer") or "")]
    layer.record_output(units, injected_ids=screen_ids)
    return dict(layer.pairs)


def merge_pairs(pair_dicts):
    merged = {}
    for pairs in pair_dicts:
        for k, d in pairs.items():
            m = merged.setdefault(k, {"weight": 0.0, "uses": 0})
            m["weight"] += float(d.get("weight", 0.0))
            m["uses"] += int(d.get("uses", 0))
    return merged


def alive_pool(eng):
    pool = [i for i, a in eng.atoms.items() if a.alive]
    pool += [i for i, n in eng.nodes.items() if n.alive and n.value]
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=os.path.join(_HERE, "out", "tree_ctx.json.gz"))
    ap.add_argument("--run", default=os.path.join(_HERE, "out", "ctx_on_u"))
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "usage_expand"))
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--anchor-budget", type=int, default=6)
    ap.add_argument("--rand-trials", type=int, default=50)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    res = json.load(open(os.path.join(args.run, "replay_result.json"), encoding="utf-8"))
    answers = res.get("answers", [])
    print(f"[expand] {len(answers)} 答案；载入树建对 ...", flush=True)

    eng0 = build_engine(args.tree)
    n_pairs = [answer_pairs(eng0, a) for a in answers]
    r_pairs = [answer_pairs(eng0, a, params={"theta_nonredundant": 1.1}) for a in answers]
    pool = alive_pool(eng0)
    print(f"[expand] 对表就绪：非冗余 {sum(len(p) for p in n_pairs)} / "
          f"无过滤 {sum(len(p) for p in r_pairs)}；卡池 {len(pool)}", flush=True)

    rng = random.Random(42)
    rows = []
    for j, ans in enumerate(answers):
        q = ans.get("q") or ""
        # 锚（fresh 引擎，避免跨题污染）
        engj = build_engine(args.tree)
        wide = engj.retrieve(q, budget=args.anchor_budget + args.topk)
        anchors = list(wide[:args.anchor_budget])
        extra = list(wide[args.anchor_budget:args.anchor_budget + args.topk])
        evidence = {c[0] for c in ans.get("cited_texts") or []}
        anchor_set = set(anchors)

        merged_n = merge_pairs([p for i, p in enumerate(n_pairs) if i != j])
        merged_r = merge_pairs([p for i, p in enumerate(r_pairs) if i != j])
        usage = intersection_bring(anchors, merged_n, topk=args.topk)
        usage_cov = coverage_bring(engj, anchor_set, merged_n, topk=args.topk)
        redund = intersection_bring(anchors, merged_r, topk=args.topk)
        hub = list(ans.get("hub") or [])
        eps_all = {x for key in merged_n for x in key}
        eps_cov = sum(1 for e in eps_all
                      if covered_by_anchors(engj, anchor_set, e))

        rand_hits = []
        for _ in range(args.rand_trials):
            picks = rng.sample(pool, min(args.topk, len(pool)))
            rand_hits.append(len(set(picks) & evidence))

        def new_hits(items):
            return len(set(items) & (evidence - anchor_set))

        rows.append({
            "id": ans.get("id"), "layer": ans.get("layer"),
            "n_evidence": len(evidence),
            "direct_hits": len(anchor_set & evidence),
            "usage_hits": len(set(usage) & evidence),
            "usage_new": new_hits(usage),
            "usage_n": len(usage),
            "usage_cov_hits": len(set(usage_cov) & evidence),
            "usage_cov_new": new_hits(usage_cov),
            "usage_cov_n": len(usage_cov),
            "pair_eps": len(eps_all), "pair_eps_cov": eps_cov,
            # 可达性上界：目标证据卡出现在其余答案边端点全集中的数量
            "reach_evidence": len(evidence & eps_all),
            "extra_hits": len(set(extra) & evidence),
            "extra_new": new_hits(extra),
            "redund_hits": len(set(redund) & evidence),
            "redund_new": new_hits(redund),
            "hub_hits": len(set(hub) & evidence),
            "hub_new": new_hits(hub),
            "rand_hits_mean": round(sum(rand_hits) / len(rand_hits), 3) if rand_hits else 0.0,
            "rand_hits_max": max(rand_hits) if rand_hits else 0,
            "usage_ids": usage, "hub_ids": hub[:8],
        })
        print(f"  {rows[-1]['id']:10s} 证据={len(evidence):2d} 锚命中={rows[-1]['direct_hits']:2d} "
              f"usage新={rows[-1]['usage_new']:2d}/{rows[-1]['usage_n']:2d} "
              f"覆盖感知新={rows[-1]['usage_cov_new']:2d}/{rows[-1]['usage_cov_n']:2d} "
              f"(端点覆盖 {rows[-1]['pair_eps_cov']}/{rows[-1]['pair_eps']}) "
              f"相似延伸新={rows[-1]['extra_new']} 冗余新={rows[-1]['redund_new']} "
              f"hub新={rows[-1]['hub_new']} 随机均={rows[-1]['rand_hits_mean']}", flush=True)

    def agg(key):
        return sum(r[key] for r in rows)

    summary = {
        "run": os.path.basename(args.run), "n_questions": len(rows),
        "topk": args.topk, "anchor_budget": args.anchor_budget,
        "evidence_total": agg("n_evidence"),
        "direct_hits": agg("direct_hits"),
        "usage_hits": agg("usage_hits"), "usage_new": agg("usage_new"),
        "usage_cov_hits": agg("usage_cov_hits"), "usage_cov_new": agg("usage_cov_new"),
        "pair_eps_total": agg("pair_eps"), "pair_eps_cov": agg("pair_eps_cov"),
        "reach_evidence": agg("reach_evidence"),
        "extra_hits": agg("extra_hits"), "extra_new": agg("extra_new"),
        "redund_hits": agg("redund_hits"), "redund_new": agg("redund_new"),
        "hub_hits": agg("hub_hits"), "hub_new": agg("hub_new"),
        "rand_hits_mean_sum": round(sum(r["rand_hits_mean"] for r in rows), 3),
        "usage_win_vs_extra": sum(1 for r in rows if r["usage_new"] > r["extra_new"]),
        "usage_win_vs_redund": sum(1 for r in rows if r["usage_new"] > r["redund_new"]),
        "usage_win_vs_hub": sum(1 for r in rows if r["usage_new"] > r["hub_new"]),
        "per_question": rows,
    }
    with open(os.path.join(args.out, "usage_expand_result.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("\n[expand] 汇总：")
    print(f"  证据总数 {summary['evidence_total']}；锚命中 {summary['direct_hits']}")
    print(f"  交集带动：命中 {summary['usage_hits']}（新增 {summary['usage_new']}）")
    print(f"  覆盖感知带动：命中 {summary['usage_cov_hits']}（新增 {summary['usage_cov_new']}）；"
          f"边端点被锚覆盖 {summary['pair_eps_cov']}/{summary['pair_eps_total']}")
    print(f"  可达性上界：目标证据出现在其余答案边端点中 {summary['reach_evidence']}"
          f"/{summary['evidence_total']}")
    print(f"  相似度延伸：命中 {summary['extra_hits']}（新增 {summary['extra_new']}）")
    print(f"  冗余边带动：命中 {summary['redund_hits']}（新增 {summary['redund_new']}）")
    print(f"  历史 hub：命中 {summary['hub_hits']}（新增 {summary['hub_new']}）")
    print(f"  随机对照：期望命中 {summary['rand_hits_mean_sum']}")
    print(f"  胜场（新增证据 > 对照）：vs 相似延伸 {summary['usage_win_vs_extra']}/{len(rows)}，"
          f"vs 冗余边 {summary['usage_win_vs_redund']}/{len(rows)}，"
          f"vs hub {summary['usage_win_vs_hub']}/{len(rows)}")
    print(f"\n[expand] 报告 → {os.path.join(args.out, 'usage_expand_result.json')}")


if __name__ == "__main__":
    main()
