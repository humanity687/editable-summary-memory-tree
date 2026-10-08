#!/usr/bin/env python3
"""usage_replay.py — Phase A：使用信号层零成本实证（A1 边冗余率 + A2 答案影子重放）。

纯离线分析，零机制改动（设计规格见 realtest/USAGE_SPEC.md §5）：
  A1  ctx_on_u 共现边（2082 β 边）→ 端点向量余弦分布 vs 随机近邻对基线；
  A2  答案逐句 → 结构感知切句 → 三档分类（T1 引用 / T2 未注入命中 / T3 改写命中 /
      复读不记）→ 与 cited_texts 证据链交集 + 非冗余 A↔B 边。

运行时与离线同源：切句/分档/覆盖/亲缘判定来自 `realtest/usage.py`（USAGE_SPEC §6）。

用法：
  python3 realtest/chatbench/usage_replay.py \
      --tree realtest/chatbench/out/tree_ctx.json.gz \
      --runs ctx_on_u,ctx_off_u \
      --out realtest/chatbench/out/usage_analysis
"""
import argparse
import json
import os
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

from realtest.realengine import RealEngine
from realtest.cooc import DEFAULTS as COOC_DEFAULTS
from realtest.chatbench.tree_io import load_tree
from realtest.usage import (
    split_units, ep_text, ep_vec, cos, tree_related,
    screen_bigrams, classify_unit,
)
from realtest.textutil import quantiles as qs


class FakeLLM:
    def chat(self, system, user):
        return "【事实】占位。", {"completion_tokens": 1}


# ── 引擎工具 ──────────────────────────────────────────────

def build_engine(tree_path):
    eng = RealEngine(llm=FakeLLM(), style_alpha=0.0, render_clean=False,
                     assembly_budget=16000, retrieval_budget=8)
    load_tree(eng, tree_path)
    return eng





# ── A1：边冗余率 ──────────────────────────────────────────

def replay_edges(jsonl_path):
    """按 op 重建终态边表（add/decay/create-reclaim；参数取 cooc.py DEFAULTS）。"""
    eps = COOC_DEFAULTS["eps"]
    reclaim = COOC_DEFAULTS["reclaim_base"]
    edges = {}
    ops = Counter()
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            op = row.get("op")
            ops[op] += 1
            if op == "add":
                a, b = tuple(row["a"]), tuple(row["b"])
                key = (a, b) if a <= b else (b, a)
                edges[key] = edges.get(key, 0.0) + float(row["delta"])
            elif op == "decay":
                d = eps * int(row.get("n", 1))
                for k in list(edges):
                    edges[k] -= d
                    if edges[k] <= 0:
                        del edges[k]
            elif op == "create":
                mem = [(m["id"], m["type"]) for m in row.get("members", [])]
                for x in range(len(mem)):
                    for y in range(x + 1, len(mem)):
                        a, b = mem[x], mem[y]
                        key = (a, b) if a <= b else (b, a)
                        if edges.get(key, 0.0) > reclaim:
                            edges[key] = reclaim
    return edges, dict(ops)


def a1_edge_redundancy(eng, cooc_dir, rng_seed=42):
    import random
    edges, ops = replay_edges(os.path.join(cooc_dir, "cooc_edges.jsonl"))
    concept_members = set()
    cpath = os.path.join(cooc_dir, "cooc_concepts.jsonl")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("op") == "create":
                    concept_members |= {(m["id"], m["type"])
                                        for m in row.get("members", [])}

    sims, missing = [], 0
    missing_types = Counter()
    strong_sims, concept_sims, other_sims = [], [], []
    ep_pool = set()
    for (a, b), v in edges.items():
        ta, tb = ep_text(eng, a), ep_text(eng, b)
        if ta is None or tb is None:
            missing += 1
            for ep, txt in ((a, ta), (b, tb)):
                if txt is None:
                    missing_types[ep[1]] += 1
            continue
        ep_pool.add(a)
        ep_pool.add(b)
        s = cos(ep_vec(eng, a), ep_vec(eng, b))
        if s is None:
            missing += 1
            continue
        sims.append(s)
        if a in concept_members or b in concept_members:
            concept_sims.append(s)
        elif v >= COOC_DEFAULTS["theta_coo"]:
            strong_sims.append(s)
        else:
            other_sims.append(s)

    rng = random.Random(rng_seed)
    pool = [ep for ep in ep_pool if ep_text(eng, ep) is not None]
    rand_runs = []
    for _ in range(5):
        rs = []
        for _ in range(len(sims)):
            x, y = rng.choice(pool), rng.choice(pool)
            if x == y:
                continue
            s = cos(ep_vec(eng, x), ep_vec(eng, y))
            if s is not None:
                rs.append(s)
        rand_runs.append(qs(rs))
    def avg(key):
        vals = [r[key] for r in rand_runs if key in r]
        return round(sum(vals) / len(vals), 4) if vals else None
    rand_summary = {k: avg(k) for k in ("mean", "p10", "p25", "p50", "p75", "p90",
                                        "frac_lt_0.4", "frac_lt_0.5")}

    result = {
        "edges_final": len(edges), "ops": ops,
        "compared": len(sims), "missing_endpoints": missing,
        "missing_endpoint_types": dict(missing_types),
        "edge_sim": qs(sims), "random_sim": rand_summary,
        "buckets": {"concept_member": qs(concept_sims),
                    "strong_affinity": qs(strong_sims),
                    "other": qs(other_sims)},
        "edge_sims_sample": [round(s, 4) for s in sims[:40]],
    }
    # θ_nonredundant 候选：边分布 p25（分位取，非绝对值）
    if sims:
        result["theta_nonredundant_proposal"] = qs(sims)["p25"]
    return result


# ── A2：答案影子重放 ──────────────────────────────────────

def screen_sets(eng, answer):
    """屏上集近似：冻结树 tiling + 该答案 direct/hub 命中卡（chatbench 注入的是命中卡
    本身，无 expand_leaves）。缺失端点计入 missing。"""
    emit = {item for _k, item, _v in eng._emit()}
    hits = set(answer.get("direct") or []) | set(answer.get("hub") or [])
    missing = 0
    for i in hits:
        if i in eng.atoms or i in eng.nodes:
            emit.add(i)
        else:
            missing += 1
    return emit, missing


def classify_answer(eng, answer, floor=0.25, topk=3, pos=None, cover_span_max=60):
    """逐句分档（调用 usage.classify_unit，与运行时同源）。返回 (rows, stats,
    screen_missing, screen_ids, cited_ids)。"""
    if pos is None:
        pos = {item: idx for idx, item in enumerate(eng.chain_ids())}
    screen_ids, screen_missing = screen_sets(eng, answer)
    scr_bg = screen_bigrams(eng, screen_ids)
    cited_ids = {c[0] for c in answer.get("cited_texts") or []}
    rows = []
    stats = Counter()
    for unit in split_units(answer.get("answer") or ""):
        row, st = classify_unit(eng, unit, screen_ids=screen_ids, scr_bg=scr_bg,
                                cited_ids=cited_ids, pos=pos, floor=floor,
                                cover_span_max=cover_span_max, topk=topk)
        stats.update(st)
        rows.append(row)
    return rows, stats, screen_missing, screen_ids, cited_ids


def derive_pairs(rows):
    """每句内（引用+匹配）端点的两两对（派生 A↔B 候选）；携带来源档。"""
    pairs = {}
    for idx, r in enumerate(rows):
        eps = list(r.get("cites") or []) + [m["id"] for m in r.get("matches") or []]
        tiers = ({"T1"} if r.get("cites") else set()) | \
                {m["tier"] for m in (r.get("matches") or [])}
        for x, y in ((a, b) for a in range(len(eps)) for b in range(a + 1, len(eps))):
            a, b = eps[x], eps[y]
            key = (a, b) if a <= b else (b, a)
            d = pairs.setdefault(key, {"tiers": set(), "units": set()})
            d["tiers"] |= tiers
            d["units"].add(idx)
    return pairs


def a2_run(eng, run_dir, floor=0.25, topk=3, theta=0.4, cover_span_max=60):
    rpath = os.path.join(run_dir, "replay_result.json")
    res = json.load(open(rpath, encoding="utf-8"))
    pos = {item: idx for idx, item in enumerate(eng.chain_ids())}
    per_answer = []
    all_stats = Counter()
    all_pairs = {}       # (a,b) -> {"tiers": set, "answers": set}
    scores_by_tier = {"T2": [], "T3": []}
    card_answers = {}    # card id -> set(answer id)（T2/T3 命中复现）
    cited_any = set()
    for ans in res.get("answers", []):
        rows, stats, scr_missing, screen_ids, cited_ids = classify_answer(
            eng, ans, floor=floor, topk=topk, pos=pos, cover_span_max=cover_span_max)
        all_stats.update(stats)
        all_stats["units_total"] += len(rows)
        cited_any |= cited_ids
        for r in rows:
            for m in r.get("matches") or []:
                scores_by_tier.setdefault(m["tier"], []).append(m["score"])
                card_answers.setdefault(m["id"], set()).add(ans.get("id"))
        pairs = derive_pairs(rows)
        for key, d in pairs.items():
            d2 = all_pairs.setdefault(key, {"tiers": set(), "answers": set()})
            d2["tiers"] |= d["tiers"]
            d2["answers"].add(ans.get("id"))
        # 该答案的证据链交集
        t2_ids = {m["id"] for r in rows for m in (r.get("matches") or [])
                  if m["tier"] == "T2"}
        t3_ids = {m["id"] for r in rows for m in (r.get("matches") or [])
                  if m["tier"] == "T3"}
        per_answer.append({
            "id": ans.get("id"), "layer": ans.get("layer"),
            "screen_size": len(screen_ids), "screen_missing": scr_missing,
            "cites": len(cited_ids),
            "units": len(rows),
            "t1_units": stats.get("units_t1", 0),
            "reread_units": stats.get("units_reread", 0),
            "t2_units": stats.get("units_t2", 0),
            "t3_units": stats.get("units_t3", 0),
            "nomatch_units": stats.get("units_nomatch", 0),
            "edges_t1": stats.get("edges_t1", 0),
            "edges_t2": stats.get("edges_t2", 0),
            "edges_t3": stats.get("edges_t3", 0),
            "t2_hit_cited": len(t2_ids & cited_ids),
            "t2_total": len(t2_ids),
            "t3_hit_cited": len(t3_ids & cited_ids),
            "t3_total": len(t3_ids),
            "t2_hit_any_example": sorted(t2_ids & cited_ids)[:5],
            "units_sample": rows[:3],
            "_t2ids": t2_ids, "_t3ids": t3_ids,
        })

    for pa in per_answer:
        t2_ids, t3_ids = pa.pop("_t2ids"), pa.pop("_t3ids")
        pa["t2_hit_cited_any"] = len(t2_ids & cited_any)
        pa["t3_hit_cited_any"] = len(t3_ids & cited_any)

    # 非冗余筛选 + 跨答案统计（非冗余 = 树亲缘外 且 端点相似度 < θ）
    pair_sims, related = {}, set()
    for key in all_pairs:
        if tree_related(eng, key[0], key[1]):
            related.add(key)
            continue
        a = (key[0], "atom" if key[0] in eng.atoms else "node")
        b = (key[1], "atom" if key[1] in eng.atoms else "node")
        s = cos(ep_vec(eng, a), ep_vec(eng, b))
        if s is not None:
            pair_sims[key] = s
    nr = {k: s for k, s in pair_sims.items() if s < theta}
    pair_sim_vals = list(pair_sims.values())
    nr_samples = []
    for k, s in sorted(nr.items(), key=lambda kv: kv[1])[:20]:
        ta = ep_text(eng, (k[0], "atom" if k[0] in eng.atoms else "node")) or ""
        tb = ep_text(eng, (k[1], "atom" if k[1] in eng.atoms else "node")) or ""
        nr_samples.append({"a": k[0], "b": k[1], "sim": round(s, 4),
                           "tiers": sorted(all_pairs[k]["tiers"]),
                           "answers": sorted(all_pairs[k]["answers"]),
                           "a_text": ta[:60], "b_text": tb[:60]})
    recurrent = sorted(((len(v), k) for k, v in card_answers.items() if len(v) >= 2),
                       key=lambda x: -x[0])[:12]
    recurrent_rows = []
    for n, i in recurrent:
        t = "atom" if i in eng.atoms else "node"
        recurrent_rows.append({"id": i, "type": t, "n_answers": n,
                               "answers": sorted(card_answers[i]),
                               "text": (ep_text(eng, (i, t)) or "")[:60]})
    summary = {
        "run": os.path.basename(run_dir),
        "n_answers": len(per_answer),
        "units": all_stats.get("units_total", 0),
        "units_t1": all_stats.get("units_t1", 0),
        "units_reread": all_stats.get("units_reread", 0),
        "units_t2": all_stats.get("units_t2", 0),
        "units_t3": all_stats.get("units_t3", 0),
        "units_nomatch": all_stats.get("units_nomatch", 0),
        "edges_t1": all_stats.get("edges_t1", 0),
        "edges_t2": all_stats.get("edges_t2", 0),
        "edges_t3": all_stats.get("edges_t3", 0),
        "match_cover_hist": {k: all_stats.get(k, 0) for k in (
            "m_self", "m_cover_le10", "m_cover_le30", "m_cover_le60",
            "m_cover_le120", "m_cover_le300", "m_cover_gt300", "m_uncovered")},
        "cover_span_max": cover_span_max,
        "match_scores": {k: qs(v) for k, v in scores_by_tier.items() if v},
        "pairs_total": len(all_pairs),
        "pairs_tree_related": len(related),
        "pairs_with_sim": len(pair_sims),
        "pairs_sim": qs(pair_sim_vals) if pair_sim_vals else {},
        "theta": theta,
        "pairs_nonredundant": len(nr),
        "pairs_cross_answer": sum(1 for d in all_pairs.values() if len(d["answers"]) > 1),
        "nonredundant_t2_involved": sum(1 for k in nr if "T2" in all_pairs[k]["tiers"]),
        "nonredundant_cross_answer": sum(1 for k in nr if len(all_pairs[k]["answers"]) > 1),
        "tiers_hist": dict(Counter(t for d in all_pairs.values() for t in d["tiers"])),
        "cited_any": sorted(cited_any)[:30],
        "cards_matched_ge2_answers": len(recurrent_rows),
        "recurrent_cards": recurrent_rows,
        "nonredundant_samples": nr_samples,
        "per_answer": per_answer,
    }
    return summary


# ── 主流程 ───────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=os.path.join(_HERE, "out", "tree_ctx.json.gz"))
    ap.add_argument("--runs", default="ctx_on_u,ctx_off_u")
    ap.add_argument("--cooc-dir", default=None,
                    help="A1 边日志目录（默认 <run>/cooc）")
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "usage_analysis"))
    ap.add_argument("--floor", type=float, default=0.25)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--cover-span", type=int, default=60,
                    help="覆盖跨度分档线：最近屏上祖先 span ≤ 此值 = T3（摘要可承载细节）")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print(f"[usage] 载入树 {args.tree} ...", flush=True)
    eng = build_engine(args.tree)
    print(f"[usage] 原子 {len(eng.atoms)} / 节点 {eng.stat()['nodes']['alive']} / "
          f"tiling {len(eng.tiling)}", flush=True)

    report = {"meta": {
        "tree": args.tree, "runs": args.runs, "floor": args.floor, "topk": args.topk,
        "note": "Phase A 影子重放（USAGE_SPEC §5）；冻结树近似，见规格已知近似声明"}}

    # A1
    run_dirs = [os.path.join(_HERE, "out", r) for r in args.runs.split(",") if r.strip()]
    on_dir = next((d for d in run_dirs if os.path.basename(d).endswith("on_u")), run_dirs[0])
    cooc_dir = args.cooc_dir or os.path.join(on_dir, "cooc")
    if os.path.exists(os.path.join(cooc_dir, "cooc_edges.jsonl")):
        print(f"[usage] A1 边冗余分析 {cooc_dir} ...", flush=True)
        report["a1"] = a1_edge_redundancy(eng, cooc_dir)
        print(f"  边 {report['a1']['edges_final']}（复算 ops={report['a1']['ops']}）"
              f" compared={report['a1']['compared']} missing={report['a1']['missing_endpoints']}")
        print(f"  edge_sim  {report['a1']['edge_sim']}")
        print(f"  random   {report['a1']['random_sim']}")
    else:
        report["a1"] = {"error": f"no cooc edges at {cooc_dir}"}

    # A2
    theta = report.get("a1", {}).get("theta_nonredundant_proposal", 0.4)
    report["a2"] = {}
    for rd in run_dirs:
        if not os.path.exists(os.path.join(rd, "replay_result.json")):
            continue
        print(f"[usage] A2 影子重放 {rd}（θ_nonredundant={theta}, "
              f"cover_span_max={args.cover_span}）...", flush=True)
        summary = a2_run(eng, rd, floor=args.floor, topk=args.topk, theta=theta,
                         cover_span_max=args.cover_span)
        report["a2"][summary["run"]] = summary
        print(f"  units={summary['units']} T1={summary['units_t1']} "
              f"复读={summary['units_reread']} T2={summary['units_t2']} "
              f"T3={summary['units_t3']} 无匹配={summary['units_nomatch']}")
        print(f"  edges T1={summary['edges_t1']} T2={summary['edges_t2']} "
              f"T3={summary['edges_t3']}；pairs={summary['pairs_total']} "
              f"亲缘外={summary['pairs_with_sim']} 非冗余={summary['pairs_nonredundant']} "
              f"跨答案={summary['pairs_cross_answer']}；复现卡(≥2答案)={summary['cards_matched_ge2_answers']}")
        print(f"  match_cover {json.dumps(summary['match_cover_hist'], ensure_ascii=False)}")
        print(f"  match_scores {json.dumps(summary['match_scores'], ensure_ascii=False)}")
        print(f"  pairs_sim {json.dumps(summary['pairs_sim'], ensure_ascii=False)}")
        for pa in summary["per_answer"]:
            print(f"    {pa['id']:10s} units={pa['units']:3d} T1={pa['t1_units']:2d} "
                  f"复读={pa['reread_units']:2d} T2={pa['t2_units']:2d} T3={pa['t3_units']:2d} "
                  f"| T2∩证据链={pa['t2_hit_cited']}/{pa['t2_total']} "
                  f"T3∩证据链={pa['t3_hit_cited']}/{pa['t3_total']}")

    with open(os.path.join(args.out, "usage_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[usage] 报告 → {os.path.join(args.out, 'usage_analysis.json')}")


if __name__ == "__main__":
    main()
