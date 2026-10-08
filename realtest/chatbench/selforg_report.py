#!/usr/bin/env python3
"""selforg_report.py — 协同层实验分析（USAGE_SPEC 协同章）。

输入：--run out/ctx_selforg2（含 replay_result.json + cooc/ + usage/）；
      --baseline out/ctx_on_u（旧 conn_weight hub 对照）。
输出：selforg_report.json + 终端摘要。

内容：
  1. 边构成（usage / recall_cross / edit）+ 各 reason 端点相似度分布 vs 随机基线；
  2. 主题节点（标题/成员数/证据序/触发输出）+ 碎片化（成员 Jaccard）；
  3. 使用审计流统计（usage/*.jsonl）；
  4. 判别：hub（交集/补齐分列）∩ 证据链 vs 旧 hub vs 随机期望；
  5. 门槛判定（主题出生 + 交集带动证据增量）。
"""
import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_REALTEST)
for _p in (_ROOT, _REALTEST):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
from realtest.usage import ep_text, ep_vec, cos
from realtest.cooc import DEFAULTS as COOC_DEFAULTS
from realtest.chatbench.usage_replay import build_engine
from realtest.textutil import quantiles as qs


def edge_analysis(eng, cooc_dir):
    edges, reason = {}, {}
    for line in open(os.path.join(cooc_dir, "cooc_edges.jsonl"), encoding="utf-8"):
        x = json.loads(line)
        if x.get("op") == "add":
            a, b = tuple(x["a"]), tuple(x["b"])
            key = (a, b) if a <= b else (b, a)
            edges[key] = edges.get(key, 0.0) + x["delta"]
            reason[key] = x.get("reason")
        elif x.get("op") == "decay":
            d = COOC_DEFAULTS["eps"] * int(x.get("n", 1))
            for k in list(edges):
                edges[k] -= d
                if edges[k] <= 0:
                    del edges[k]
    sims = defaultdict(list)
    missing = Counter()
    pool = set()
    for key, r in reason.items():
        if key not in edges:
            continue
        a, b = key
        if ep_text(eng, a) is None or ep_text(eng, b) is None:
            missing[r] += 1
            continue
        s = cos(ep_vec(eng, a), ep_vec(eng, b))
        if s is None:
            missing[r] += 1
            continue
        sims[r].append(s)
        pool.add(a)
        pool.add(b)
    rng = random.Random(42)
    pool = [ep for ep in pool if ep_text(eng, ep) is not None]
    n_all = sum(len(v) for v in sims.values())
    rand = []
    for _ in range(n_all):
        x, y = rng.choice(pool), rng.choice(pool)
        if x == y:
            continue
        s = cos(ep_vec(eng, x), ep_vec(eng, y))
        if s is not None:
            rand.append(s)
    return {"final_edges": len(edges),
            "final_by_reason": dict(Counter(reason[k] for k in edges)),
            "sim_by_reason": {r: qs(v) for r, v in sims.items()},
            "random_sim": qs(rand), "missing_by_reason": dict(missing)}


def intersection_floor_sensitivity(run_dir, floors=(0.0, 2.0, 4.0, 6.0, 10.0)):
    """交集通道的稳定门槛敏感性：候选 = 概念成员中与 direct 锚有边（权 ≥ floor）者
    （忽略 top-j/k 选择的上界口径）。低权单次共现边若贡献全部命中，说明不可救。"""
    edges = {}
    for line in open(os.path.join(run_dir, "cooc", "cooc_edges.jsonl"), encoding="utf-8"):
        x = json.loads(line)
        if x.get("op") == "add":
            a, b = tuple(x["a"]), tuple(x["b"])
            key = (a, b) if a <= b else (b, a)
            edges[key] = edges.get(key, 0.0) + x["delta"]
        elif x.get("op") == "decay":
            d = COOC_DEFAULTS["eps"] * int(x.get("n", 1))
            for k in list(edges):
                edges[k] -= d
                if edges[k] <= 0:
                    del edges[k]
    members = set()
    for line in open(os.path.join(run_dir, "cooc", "cooc_concepts.jsonl"),
                     encoding="utf-8"):
        x = json.loads(line)
        if x.get("op") == "create":
            members |= {(m["id"], m["type"]) for m in x.get("members", [])}
    res = {}
    answers = json.load(open(os.path.join(run_dir, "replay_result.json"),
                             encoding="utf-8")).get("answers", [])
    for floor in floors:
        cand_n = hit = new = 0
        for a in answers:
            ev = {c[0] for c in a.get("cited_texts") or []}
            direct = set(a.get("direct") or [])
            cand = set()
            for (x, y), v in edges.items():
                if v < floor:
                    continue
                if x[0] in direct and y in members and y[0] not in direct:
                    cand.add(y[0])
                elif y[0] in direct and x in members and x[0] not in direct:
                    cand.add(x[0])
            cand_n += len(cand)
            hit += len(cand & ev)
            new += len(cand & (ev - direct))
        res[str(floor)] = {"candidates": cand_n, "evidence_hits": hit,
                           "new_evidence": new}
    return res


def concept_analysis(cooc_dir):
    rows = []
    cp = os.path.join(cooc_dir, "cooc_concepts.jsonl")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f]
    creates = [r for r in rows if r.get("op") == "create"]
    concepts = []
    for r in creates:
        members = r.get("members", [])
        concepts.append({
            "id": r.get("concept"), "title": r.get("title"),
            "member_count": len(members),
            "evidence_top": [(m["id"], m.get("evidence", 0.0)) for m in members[:3]],
            "evidence_total": round(sum(m.get("evidence", 0.0) for m in members), 2),
            "trigger_outputs": r.get("trigger_outputs", []),
            "trigger_queries": r.get("trigger_queries", [])[:3],
        })
    # 碎片化：成员集合 Jaccard（>0.3 视为近重复候选）
    frag = []
    for i in range(len(creates)):
        for j in range(i + 1, len(creates)):
            sa = {(m["id"], m["type"]) for m in creates[i]["members"]}
            sb = {(m["id"], m["type"]) for m in creates[j]["members"]}
            jac = len(sa & sb) / max(1, len(sa | sb))
            if jac >= 0.2:
                frag.append({"a": creates[i]["concept"], "b": creates[j]["concept"],
                             "jaccard": round(jac, 3)})
    ops = Counter(r.get("op") for r in rows)
    return {"count": len(concepts), "concepts": concepts,
            "member_jaccard_ge0.2": frag, "ops": dict(ops)}


def usage_analysis(run_dir):
    out = {}
    for name, key in (("usage_outputs.jsonl", "outputs"),
                      ("usage_edges.jsonl", "edges"),
                      ("usage_pairs.jsonl", "pairs")):
        p = os.path.join(run_dir, "usage", name)
        if os.path.exists(p):
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
            out[key] = len(rows)
            if key == "outputs":
                out["tiers"] = dict(Counter(r.get("tier") for r in rows))
    return out


def discrimination(run_dir, baseline_dir):
    new = json.load(open(os.path.join(run_dir, "replay_result.json"), encoding="utf-8"))
    old = {}
    bp = os.path.join(baseline_dir, "replay_result.json")
    if os.path.exists(bp):
        old = {a["id"]: a for a in json.load(open(bp, encoding="utf-8"))["answers"]}
    rows = []
    for a in new.get("answers", []):
        ev = {c[0] for c in a.get("cited_texts") or []}
        direct = set(a.get("direct") or [])
        hub = list(a.get("hub") or [])
        detail = {d[0]: d[1] for d in a.get("hub_detail") or []}
        inter = [i for i in hub if detail.get(i) == "intersection"]
        fall = [i for i in hub if detail.get(i) == "fallback"]
        o = old.get(a["id"], {})
        ohub = list(o.get("hub") or [])
        odirect = set(o.get("direct") or [])
        rows.append({
            "id": a["id"], "n_evidence": len(ev),
            "direct_hit": len(direct & ev),
            "hub_n": len(hub),
            "inter_n": len(inter), "inter_hit": len(set(inter) & ev),
            "inter_new": len(set(inter) & (ev - direct)),
            "fall_n": len(fall), "fall_hit": len(set(fall) & ev),
            "fall_new": len(set(fall) & (ev - direct)),
            "old_hub_hit": len(set(ohub) & ev),
            "old_hub_new": len(set(ohub) & (ev - odirect)),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=os.path.join(_HERE, "out", "tree_ctx.json.gz"))
    ap.add_argument("--run", default=os.path.join(_HERE, "out", "ctx_selforg2"))
    ap.add_argument("--baseline", default=os.path.join(_HERE, "out", "ctx_on_u"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_dir = args.out or args.run
    os.makedirs(out_dir, exist_ok=True)

    eng = build_engine(args.tree)
    report = {
        "run": os.path.basename(args.run),
        "edges": edge_analysis(eng, os.path.join(args.run, "cooc")),
        "concepts": concept_analysis(os.path.join(args.run, "cooc")),
        "usage_audit": usage_analysis(args.run),
        "discrimination": discrimination(args.run, args.baseline),
        "intersection_floor_sensitivity": intersection_floor_sensitivity(args.run),
    }
    d = report["discrimination"]
    report["gates"] = {
        "themes_born": report["concepts"]["count"] > 0,
        "intersection_new_evidence": sum(r["inter_new"] for r in d),
        "fallback_new_evidence": sum(r["fall_new"] for r in d),
        "old_hub_new_evidence": sum(r["old_hub_new"] for r in d),
        "verdict": None,
    }
    g = report["gates"]
    g["verdict"] = ("pass" if g["themes_born"] and g["intersection_new_evidence"] > 0
                    else "fail")
    with open(os.path.join(out_dir, "selforg_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    e = report["edges"]
    print(f"[selforg] 边 {e['final_edges']} by reason {e['final_by_reason']}")
    for r, q in e["sim_by_reason"].items():
        print(f"  {r}: {q}")
    print(f"  random: {e['random_sim']}")
    print(f"[selforg] 主题节点 {report['concepts']['count']} 个；"
          f"近重复对(成员Jaccard≥0.2) {len(report['concepts']['member_jaccard_ge0.2'])}")
    for c in report["concepts"]["concepts"]:
        print(f"  c{c['id']} {c['title']} members={c['member_count']} "
              f"evidence={c['evidence_total']} outputs={c['trigger_outputs'][:1]}")
    print(f"[selforg] 使用审计 {report['usage_audit']}")
    print(f"[selforg] 交集稳定门槛敏感性："
          f"{json.dumps(report['intersection_floor_sensitivity'], ensure_ascii=False)}")
    print(f"[selforg] 判别（新增证据）：交集 {g['intersection_new_evidence']} / "
          f"补齐 {g['fallback_new_evidence']} / 旧hub {g['old_hub_new_evidence']}")
    for r in d:
        print(f"  {r['id']:12s} ev={r['n_evidence']:2d} direct={r['direct_hit']} "
              f"inter={r['inter_hit']}(new {r['inter_new']})/{r['inter_n']} "
              f"fall={r['fall_hit']}(new {r['fall_new']})/{r['fall_n']} "
              f"old={r['old_hub_hit']}(new {r['old_hub_new']})")
    print(f"[selforg] 门槛判定：{g['verdict'].upper()}")
    print(f"[selforg] 报告 → {os.path.join(out_dir, 'selforg_report.json')}")


if __name__ == "__main__":
    main()
