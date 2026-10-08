#!/usr/bin/env python3
"""score_compare.py — 四臂（tree_off/tree_on/raptor/fullsend）统一评分对照。

复用 score.py 的评分函数；judge = deepseek-v4-flash + thinking。
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
sys.path.insert(0, _REALTEST)
sys.path.insert(0, _HERE)

import score as S  # noqa: E402
from llmclient import DeepSeekClient  # noqa: E402

ARMS = ["tree_off", "tree_on", "raptor", "fullsend"]


def avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.join(_HERE, "out"))
    ap.add_argument("--questions", default=os.path.join(_HERE, "questions_ctx.json"))
    ap.add_argument("--reference", default=os.path.join(_HERE, "reference_claims.json"))
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--judge-thinking-budget", type=int, default=2048)
    ap.add_argument("--max-refs", type=int, default=12)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    d = cfg.get("deepseek", {})
    llm = DeepSeekClient(base_url=d.get("base_url"), model=d.get("model", "deepseek-v4-flash"),
                         api_key=d.get("api_key"), temperature=0.0, timeout=600,
                         thinking_budget=args.judge_thinking_budget)
    qs = json.load(open(args.questions, encoding="utf-8"))
    ref = []
    rc = json.load(open(args.reference, encoding="utf-8"))
    for fn, cs in rc.items():
        for c in cs:
            c["source"] = fn
            ref.append(c)

    out = args.out or os.path.join(args.base, "cmp_score.json")

    def dump(partial):
        payload = {"_meta": {
            "judge_model": getattr(llm, "model", "?"),
            "judge_thinking_budget": args.judge_thinking_budget,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "partial": partial, "arms_done": list(res.keys()),
        }}
        payload.update(res)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"[write] {out} partial={partial} arms={list(res.keys())}", flush=True)

    res = {}
    for arm in ARMS:
        p = os.path.join(args.base, f"cmp_{arm}", "replay_result.json")
        if not os.path.exists(p):
            print(f"[skip] {arm}: {p} 不存在")
            continue
        r = json.load(open(p, encoding="utf-8"))
        ans = r["answers"]
        print(f"[scoring] {arm} (answerer={r.get('model')}, n={len(ans)})", flush=True)
        t1 = S.score_t1(llm, qs.get("t1", []), ans)
        t2 = S.score_t2(llm, qs.get("t2", []), ans)
        t2r = S.score_t2_ref(llm, ref, ans, args.max_refs)
        t3 = S.score_t3(ans)
        cite_valid = sum(a["cite_check"]["valid_ids"] for a in ans)
        cite_total = sum(a["cite_check"]["citations"] for a in ans)
        quoted = sum(a["cite_check"].get("quoted", 0) for a in ans)
        quote_in_cited = sum(a["cite_check"].get("quote_in_cited", 0) for a in ans)
        quotes_total = sum(a["cite_check"].get("quotes_total", 0) for a in ans)
        quote_any = sum(a["cite_check"].get("quote_any", 0) for a in ans)
        ans_chars = sum(a["cite_check"].get("chars", len(a["answer"] or "")) for a in ans)
        t2_chars = sum(len(a["answer"] or "") for a in ans if a.get("layer") == "t2")
        res[arm] = {
            "meta": r.get("meta"), "elapsed_s": r.get("elapsed_s"),
            "answerer_model": r.get("model"),
            "t2_coverage": avg(t2, "citation_coverage"),
            "t2_support": avg(t2, "supported_ratio"),
            "t2_vs_analysis_coverage": avg(t2r, "coverage"),
            "t2_vs_analysis_contradiction": avg(t2r, "contradiction"),
            "t3_accuracy": avg(t3, "accuracy"),
            "cite_valid_rate": round(cite_valid / cite_total, 3) if cite_total else 0,
            "quote_in_cited_rate": round(quote_in_cited / quoted, 3) if quoted else 0,
            "quote_any_rate": round(quote_any / quotes_total, 3) if quotes_total else 0,
            "citations_per_1k_chars": round(1000 * cite_total / ans_chars, 1) if ans_chars else 0,
            "t2_chars": t2_chars,
            "raw": {"t1": t1, "t2": t2, "t2_ref": t2r, "t3": t3},
        }
        print(f"[scored] {arm}", flush=True)
        dump(True)          # 逐臂增量写盘：中途被杀也保留已完成部分

    dump(False)
    print("\n================ 四臂对比（统一口径）================")
    hdr = (f"{'arm':10s} {'T3一致':>7s} {'T2覆盖':>7s} {'T2支持':>7s} {'T2对照':>7s} "
           f"{'引用有效':>8s} {'引号属原msg':>10s} {'引号可验':>8s} {'引/千字':>7s} {'T2字数':>6s}")
    print(hdr)
    for arm in ARMS:
        if arm not in res:
            continue
        r = res[arm]
        print(f"{arm:10s} {str(r['t3_accuracy']):>7s} {str(r['t2_coverage']):>7s} "
              f"{str(r['t2_support']):>7s} {str(r['t2_vs_analysis_coverage']):>7s} "
              f"{str(r['cite_valid_rate']):>8s} {str(r['quote_in_cited_rate']):>10s} "
              f"{str(r['quote_any_rate']):>8s} {str(r['citations_per_1k_chars']):>7s} "
              f"{str(r['t2_chars']):>6s}")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
