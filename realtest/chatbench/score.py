#!/usr/bin/env python3
"""score.py — ChatBench 评分：双通道 T1 + claim 分解 T2 + 确定性引用校验。

用法：
  python3 realtest/chatbench/score.py --off out/ms_off/replay_result.json \
      --on out/ms_on/replay_result.json --questions .../questions_absence.json \
      --out .../score.json

通道：
  T1 精确通道（gold 片段关键词）+ 语义通道（LLM judge 等价）+ 引用真实性；
      AB 题按“正确拒答”判分。
  T2 claim 分解：拆 claim → 引用覆盖 → 逐 claim 支持度（确定性硬事实 + LLM 判定）。
纪律：judge = deepseek-v4-flash **开思考**（budget 限量，独立于被测的 no-think 配置）；
分歧样本进人工复核队列。（judge 与被测同底座属已知局限，报告须注明。）
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
sys.path.insert(0, _REALTEST)
from llmclient import DeepSeekClient  # noqa: E402
from textutil import norm_cjk as norm  # noqa: E402

REFUSE = re.compile(r"无此信息|没有(这段|相关)?(信息|记录)|未提及|不知道|无法确定|记忆流中(并)?无")


def llm_text(llm, system, user):
    try:
        t, _u, _l = llm.chat(system, user)
        return t or ""
    except Exception as e:
        return f"[judge error: {e}]"


def llm_yes(llm, system, user):
    return "是" in llm_text(llm, system, user)[:10]


def parse_claim_objs(text):
    """claim 分解容错解析：优先 [{"claim":...,"evidence":[...]}]，退化到字符串数组。"""
    m = re.search(r"\[.*\]", text or "", re.S)
    out = []
    if m:
        try:
            d = json.loads(m.group(0))
            for x in d:
                if isinstance(x, dict):
                    c = str(x.get("claim") or x.get("text") or "").strip()
                    ev = [str(e) for e in (x.get("evidence") or [])]
                    if c:
                        out.append((c, ev))
                elif isinstance(x, str) and x.strip():
                    out.append((x.strip(), []))
        except Exception:
            pass
    if not out:
        out = [(l.strip("-• \t"), []) for l in (text or "").splitlines() if l.strip()][:20]
    return out


def score_t1(llm, items, answers):
    by_id = {a["id"]: a for a in answers}
    out = []
    for it in items:
        a = by_id.get(it["id"])
        if not a:
            continue
        ans = a["answer"]
        is_ab = it.get("type") == "AB"
        if is_ab:
            correct = bool(REFUSE.search(ans)) and not re.search(r"(叫|名字是|岁)", ans)
            rec = {"id": it["id"], "type": "AB", "correct": correct,
                   "exact": correct, "semantic": correct,
                   "cite_valid": a["cite_check"]["valid_ids"],
                   "cite_total": a["cite_check"]["citations"],
                   "cite_atom": a["cite_check"].get("atom", 0)}
        else:
            exact = any(norm(f) in norm(ans) for f in it.get("gold_frags", []))
            semantic = exact
            if not exact and it.get("gold_text"):
                semantic = llm_yes(
                    llm, "你是严格的评分员，只回答『是』或『否』。",
                    f"标准答案：{it['gold_text']}\n模型答案：{ans[:800]}\n"
                    f"模型答案是否在事实上与标准答案一致（允许措辞不同）？")
            rec = {"id": it["id"], "type": it.get("type", "a"),
                   "correct": bool(exact or semantic), "exact": bool(exact),
                   "semantic": bool(semantic),
                   "cite_valid": a["cite_check"]["valid_ids"],
                   "cite_total": a["cite_check"]["citations"],
                   "cite_atom": a["cite_check"].get("atom", 0)}
        out.append(rec)
    return out


def score_t2(llm, items, answers):
    by_id = {a["id"]: a for a in answers}
    out = []
    for it in items:
        a = by_id.get(it["id"])
        if not a:
            continue
        claims = parse_claim_objs(llm_text(
            llm, "你是严谨的分析助手。把给定回答拆成可独立判真伪的原子 claim。"
                 "对每条 claim，抽取回答中支持它的引用 ID（形如 #123 或 N456；"
                 "没有引用就给空数组）。只输出 JSON 数组："
                 '[{"claim":"...","evidence":["#123"]}, ...]，不要解释。',
            a["answer"][:3000]))
        cited_map = {str(c[0]): c[2] for c in a.get("cited_texts", [])}
        n_cited = n_supported = n_hard_ok = n_hard = 0
        detail = []
        for c, ev_ids in claims[:12]:
            evs = " ".join(str(e) for e in ev_ids)
            ids = (re.findall(r"msg:([0-9a-fA-F]{6,12})", evs)
                   or re.findall(r"[#N](\d+)", evs)
                   or re.findall(r"[#N](\d+)", c))
            ev = [cited_map[i] for i in ids if i in cited_map]
            if ids and ev:
                n_cited += 1
            verdict = "无引用"
            if ev:
                verdict = llm_text(
                    llm, "你是严格的证据审核员。只回答『支持』『不支持』或『无关』。",
                    f"依据：\n" + "\n".join(f"- {e}" for e in ev) +
                    f"\n\n断言：{c}\n依据是否支持该断言？")
                verdict = next((v for v in ("支持", "不支持", "无关") if v in verdict), "无关")
            hard = re.findall(r"\d+(?:[\.\-/月日年]\d+)*|[A-Za-z][A-Za-z0-9_-]{2,}", c)
            if hard:
                n_hard += 1
                if any(any(h in e for e in ev) for h in hard):
                    n_hard_ok += 1
            if verdict == "支持":
                n_supported += 1
            detail.append({"claim": c[:120], "verdict": verdict})
        out.append({"id": it["id"], "n_claims": len(claims),
                    "citation_coverage": round(n_cited / len(claims), 3) if claims else 0,
                    "supported_ratio": round(n_supported / len(claims), 3) if claims else 0,
                    "hard_fact_accuracy": round(n_hard_ok / n_hard, 3) if n_hard else None,
                    "detail": detail})
    return out


def score_t2_ref(llm, ref_claims, answers, max_refs=12):
    """T2 对照已有分析：生成报告对参照 claim 的覆盖/矛盾率（LLM judge 三分类）。"""
    refs = [c["claim"] for c in ref_claims][:max_refs]
    out = []
    for a in answers:
        if a.get("layer") != "t2":
            continue
        covered = contradicted = 0
        detail = []
        for c in refs:
            v = llm_text(llm, "你是严格的报告比对员。只回答『包含』『矛盾』或『未提』。",
                         f"【参考结论】{c}\n【待评报告】\n{a['answer'][:2500]}\n"
                         f"该报告是否包含/支持参考结论？")
            verdict = next((x for x in ("包含", "矛盾", "未提") if x in v), "未提")
            covered += verdict == "包含"
            contradicted += verdict == "矛盾"
            detail.append({"ref": c[:60], "verdict": verdict})
        out.append({"id": a["id"], "n_ref": len(refs), "covered": covered,
                    "contradicted": contradicted,
                    "coverage": round(covered / len(refs), 3) if refs else 0,
                    "contradiction": round(contradicted / len(refs), 3) if refs else 0,
                    "detail": detail})
    return out


def score_t3(answers):
    """T3 报告验证：逐条判定 支持/反驳/无据 与金标一致率 + 引用真实性。"""
    out = []
    for a in answers:
        if a.get("layer") != "t3":
            continue
        verdicts = {}
        for m in re.finditer(r"(\d+)\s*[\.、:：]\s*(支持|反驳|无据)", a["answer"]):
            verdicts[int(m.group(1))] = m.group(2)
        n = min(len(a["claims"]), len(a["labels"]))
        correct = 0
        detail = []
        for i in range(n):
            got = verdicts.get(i + 1, "未判")
            exp = {"support": "支持", "refute": "反驳", "unsupported": "无据"}.get(a["labels"][i], "支持")
            ok = got == exp
            correct += ok
            detail.append({"claim": a["claims"][i][:60], "exp": exp, "got": got})
        out.append({"id": a["id"], "n": n, "correct": correct,
                    "accuracy": round(correct / n, 3) if n else 0,
                    "cite_valid": a["cite_check"]["valid_ids"],
                    "cite_total": a["cite_check"]["citations"], "detail": detail})
    return out


def agg(t1):
    d = {}
    for r in t1:
        k = r["type"]
        d.setdefault(k, [0, 0])
        d[k][0] += 1 if r["correct"] else 0
        d[k][1] += 1
    tot = [sum(v[0] for v in d.values()), sum(v[1] for v in d.values())]
    out = {k: f"{v[0]}/{v[1]}" for k, v in d.items()}
    out["ALL"] = f"{tot[0]}/{tot[1]}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--off", required=True)
    ap.add_argument("--on", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--judge-model", default="deepseek-v4-flash")
    ap.add_argument("--judge-thinking-budget", type=int, default=2048,
                    help="judge 开思考并限量（>0 才启用；deepseek-v4-flash）")
    ap.add_argument("--judge-temp", type=float, default=0.0)
    ap.add_argument("--reference", default=os.path.join(_HERE, "reference_claims.json"),
                    help="T2 对照用的已有分析 claim（make_reference.py 生成）")
    ap.add_argument("--max-refs", type=int, default=12)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    d = cfg.get("deepseek", {})
    llm = DeepSeekClient(
        base_url=d.get("base_url", "https://api.deepseek.com/v1"),
        model=args.judge_model, api_key=d.get("api_key"),
        temperature=args.judge_temp, timeout=d.get("timeout", 600),
        thinking_budget=args.judge_thinking_budget)
    qs = json.load(open(args.questions, encoding="utf-8"))
    off = json.load(open(args.off, encoding="utf-8"))
    on = json.load(open(args.on, encoding="utf-8"))

    ref = []
    if os.path.exists(args.reference):
        rc = json.load(open(args.reference, encoding="utf-8"))
        for fn, cs in rc.items():
            for c in cs:
                c["source"] = fn
                ref.append(c)

    res = {"judge_model": getattr(llm, "model", "?"),
           "judge_thinking_budget": args.judge_thinking_budget,
           "off": {"t1": score_t1(llm, qs.get("t1", []), off["answers"]),
                   "t2": score_t2(llm, qs.get("t2", []), off["answers"]),
                   "t2_ref": score_t2_ref(llm, ref, off["answers"], args.max_refs),
                   "t3": score_t3(off["answers"])},
           "on": {"t1": score_t1(llm, qs.get("t1", []), on["answers"]),
                  "t2": score_t2(llm, qs.get("t2", []), on["answers"]),
                  "t2_ref": score_t2_ref(llm, ref, on["answers"], args.max_refs),
                  "t3": score_t3(on["answers"])}}

    def _avg(rows, key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    res["summary"] = {
        "off_t1": agg(res["off"]["t1"]), "on_t1": agg(res["on"]["t1"]),
        "off_t2_ref": {"coverage": _avg(res["off"]["t2_ref"], "coverage"),
                       "contradiction": _avg(res["off"]["t2_ref"], "contradiction")},
        "on_t2_ref": {"coverage": _avg(res["on"]["t2_ref"], "coverage"),
                      "contradiction": _avg(res["on"]["t2_ref"], "contradiction")},
        "off_t3_acc": _avg(res["off"]["t3"], "accuracy"),
        "on_t3_acc": _avg(res["on"]["t3"], "accuracy"),
        "off_meta": {k: off.get(k) for k in ("mode", "atoms", "nodes", "elapsed_s", "n_messages")},
        "on_meta": {k: on.get(k) for k in ("mode", "atoms", "nodes", "elapsed_s", "n_messages")},
    }
    out = args.out or os.path.join(os.path.dirname(args.on), "score.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    print("\nT2 evidence (生成报告内部指标):")
    for arm in ("off", "on"):
        for r in res[arm]["t2"]:
            print(f"  {arm} {r['id']}: claims={r['n_claims']} cover={r['citation_coverage']} "
                  f"support={r['supported_ratio']} hard={r['hard_fact_accuracy']}")
    print("\nT2 vs 已有分析 (对照):")
    for arm in ("off", "on"):
        for r in res[arm]["t2_ref"]:
            print(f"  {arm} {r['id']}: 覆盖 {r['covered']}/{r['n_ref']} "
                  f"({r['coverage']}) 矛盾 {r['contradiction']}")
    print("\nT3 报告验证:")
    for arm in ("off", "on"):
        for r in res[arm]["t3"]:
            print(f"  {arm} {r['id']}: {r['correct']}/{r['n']} = {r['accuracy']}")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
