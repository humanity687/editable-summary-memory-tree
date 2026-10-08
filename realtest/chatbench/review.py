#!/usr/bin/env python3
"""review.py — T2 对照评分的人工抽检底稿。

对每条参照 claim 的 judge 判定（包含/矛盾/未提），附上生成报告中最相关的句子
（bigram Jaccard 最大），让人快速判断是"真没提"还是"提了但换了说法"。

用法：
  python3 realtest/chatbench/review.py \
      --score out/ctx_score_q.json \
      --off out/ctx_off_q/replay_result.json \
      --on out/ctx_on_q/replay_result.json \
      --out out/review_t2.md
"""
import argparse
import json
import os
import re
import sys
from itertools import combinations

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from textutil import bigrams_strict as bigrams  # noqa: E402


def best_sentence(ref, report):
    rb = bigrams(ref)
    best, score = "", 0.0
    for s in re.split(r"[。！？\n]+", report or ""):
        s = s.strip()
        if len(s) < 4:
            continue
        sb = bigrams(s)
        if not sb:
            continue
        j = len(rb & sb) / max(len(rb | sb), 1)
        if j > score:
            best, score = s, j
    return best, round(score, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", required=True)
    ap.add_argument("--off", required=True)
    ap.add_argument("--on", required=True)
    ap.add_argument("--out", default=os.path.join(_HERE, "out", "review_t2.md"))
    args = ap.parse_args()

    sc = json.load(open(args.score, encoding="utf-8"))
    replays = {"off": json.load(open(args.off, encoding="utf-8")),
               "on": json.load(open(args.on, encoding="utf-8"))}
    lines = ["# T2 对照抽检（生成报告 vs 已有分析）\n",
             "> 每条参照 claim 的 judge 判定 + 报告中最相关句子（bigram Jaccard 最大）。\n"]
    suspects = []
    for arm in ("off", "on"):
        answers = {a["id"]: a for a in replays[arm]["answers"] if a.get("layer") == "t2"}
        for r in sc[arm]["t2_ref"]:
            rep = answers.get(r["id"], {}).get("answer", "")
            lines.append(f"\n## {arm} · {r['id']} — 覆盖 {r['covered']}/{r['n_ref']}\n")
            for d in r["detail"]:
                ref, verdict = d["ref"], d["verdict"]
                sent, ov = best_sentence(ref, rep)
                lines.append(f"- **[{verdict}]** {ref}")
                lines.append(f"  - 最相关句({ov}): {sent[:120] or '（无）'}")
                if verdict == "未提" and ov >= 0.35:
                    suspects.append((arm, r["id"], ov, ref, sent))
            lines.append(f"\n<details><summary>生成报告全文（{r['id']}）</summary>\n\n{rep}\n\n</details>\n")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"→ {args.out}")
    print(f"\n可疑'未提'（报告里有高重叠句却判未提）{len(suspects)} 条：")
    for arm, qid, ov, ref, sent in sorted(suspects, key=lambda x: -x[2])[:12]:
        print(f"  [{arm} {qid}] ov={ov} | ref: {ref[:60]}")
        print(f"      匹配句: {sent[:80]}")


if __name__ == "__main__":
    main()
