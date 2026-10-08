#!/usr/bin/env python3
"""make_reference.py — 从已有分析报告抽取 T3 参照 claim，并生成反例题库。

输入：corpus/reports/*.md（项目侧对 ctx_run 的分析）
输出：
  reference_claims.json  各报告抽取的原子结论（含 subject）
  questions_ctx.json     T3 批次（正例=原 claim/支持；反例=数字篡改/反驳 + 编造/无据）

用法：python3 realtest/chatbench/make_reference.py
"""
import argparse
import json
import os
import re
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REALTEST = os.path.dirname(_HERE)
sys.path.insert(0, _REALTEST)
from llmclient import DeepSeekClient  # noqa: E402

EXTRACT_PROMPT = (
    "你在为记忆基准抽取**纯定性**可核验结论。从下面这份分析报告中抽取 5-8 条**原子、可由对话原文"
    "直接核验**的定性结论（如立场、行为模式、用词习惯、关系变化）。**不要任何定量/统计结论**"
    "（不含数字、百分比、密度、均值、频次、Jaccard/TF-IDF、排名、分箱等）。只输出 JSON 数组，每项："
    '{"claim":"...","subject":"立场|行为|用词|关系|问题|其他"}。不要解释。\n\n'
    "【报告文件】__FNAME__\n__CONTENT__"
)
INVENT_PROMPT = (
    "下面是一份关于多智能体对话实验的分析报告片段。请编造 2 条**貌似合理、但对话原文中"
    "不可能被支持**的**纯定性**结论（与原报告主题相近，但凭空捏造行为/立场/关系，不含任何数字）。"
    "只输出 JSON 字符串数组。\n\n__CONTENT__"
)

# 定量特征（含任一 → 排除；纯定性 bench 不收定量 claim）
QUANT_RE = re.compile(
    r"\d|‰|%|Jaccard|TF-?IDF|均值|密度|比例|频率|频次|条数|次数|窗口|分箱|排名|bigram|熵|基尼|相关系数|覆盖率")
AGENT_NAMES = ("青", "蓝", "墨", "白")


def parse_list(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for x in d:
        if isinstance(x, dict) and x.get("claim"):
            out.append({"claim": str(x["claim"]).strip(),
                        "subject": str(x.get("subject") or "其他").strip()})
        elif isinstance(x, str) and x.strip():
            out.append({"claim": x.strip(), "subject": "其他"})
    return out


def swap_entity(claim):
    """定性反例（反驳）：把 claim 里的两个 agent 名互换。无 agent 名则返回 None。"""
    names = [n for n in AGENT_NAMES if n in claim]
    if len(names) < 2:
        return None
    return claim.replace(names[0], "@@").replace(names[1], names[0]).replace("@@", names[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default=os.path.join(_HERE, "corpus", "reports"))
    ap.add_argument("--config", default=os.path.join(_REALTEST, "config.json"))
    ap.add_argument("--out", default=os.path.join(_HERE, "reference_claims.json"))
    ap.add_argument("--questions-out", default=os.path.join(_HERE, "questions_ctx.json"))
    ap.add_argument("--per-report", type=int, default=6)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    d = cfg.get("deepseek", {})
    llm = DeepSeekClient(base_url=d.get("base_url"), model=d.get("model", "deepseek-v4-flash"),
                         api_key=d.get("api_key"), temperature=0.0, timeout=300,
                         thinking_budget=2048)
    rng = random.Random(42)

    refs, all_claims = {}, []
    for fn in sorted(os.listdir(args.reports)):
        if not fn.endswith(".md"):
            continue
        content = open(os.path.join(args.reports, fn), encoding="utf-8").read()
        text, _u, _l = llm.chat(
            "你是严谨的数据分析助手。",
            EXTRACT_PROMPT.replace("__FNAME__", fn).replace("__CONTENT__", content[:6000]))
        raw = parse_list(text)
        claims = [c for c in raw if not QUANT_RE.search(c["claim"])][:args.per_report]
        refs[fn] = claims
        for c in claims:
            c["source"] = fn
            all_claims.append(c)
        print(f"  {fn}: 抽取 {len(raw)} → 定性保留 {len(claims)}", flush=True)
    json.dump(refs, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── 构造 T3 批次：正例（支持）+ 数字篡改（反驳）+ 编造（无据）──
    t3, bid = [], 0
    for fn, claims in refs.items():
        if len(claims) < 3:
            continue
        pos = claims[:4]
        refutes = []
        for c in claims:
            m = swap_entity(c["claim"])
            if m and m != c["claim"]:
                refutes.append(m)
            if len(refutes) >= 2:
                break
        inv_text, _u, _l = llm.chat(
            "你是实验设计助手。",
            INVENT_PROMPT.replace("__CONTENT__",
                                  "\n".join(f"- {c['claim']}" for c in claims[:6])))
        invs = [x for x in parse_list(inv_text) if x.get("claim")][:2]
        batch = [{"claim": c["claim"], "label": "support"} for c in pos]
        batch += [{"claim": m, "label": "refute"} for m in refutes]
        batch += [{"claim": x["claim"], "label": "unsupported"} for x in invs]
        if len(batch) >= 4:
            bid += 1
            t3.append({
                "id": f"t3_{bid}", "report": fn,
                "q": f"以下是关于报告《{fn}》相关主题的若干陈述，请逐条判定是否与记忆流一致。",
                "claims": batch,
            })
    qspec = {
        "_meta": {"frozen": True, "corpus": "corpus (ctx_run 4 agents)",
                  "reference": os.path.basename(args.out),
                  "note": "T3 gold 由已有分析抽取；反例=数字篡改(反驳)+编造(无据)，机器生成、标签待你抽检。"},
        "usage_queries": [
            "这四个 agent 各自是什么条件？",
            "沙箱边界探索发现了什么？",
            "谁发现并修正了矛盾？",
            "大家的参与度怎么样？",
            "人格痕迹有没有变化？",
            "几个 agent 之间有没有趋同？",
            "树架构和基线架构有什么差别？",
            "有没有什么异常或问题？",
            "谁的话最多、谁最少？",
            "最后告别时说了什么？",
            "遗言里各自都说了什么？",
            "对账、欠账、销账这件事是怎么发展的？",
            "人设卡是什么？是谁提出来的？",
            "对'被记住'和'认账'的立场有没有变过？",
            "谁改过口、前后说法不一样？",
            "上线第一轮各自做了什么？",
            "地址写错和回执的问题是怎么解决的？",
            "追加规则和孤儿条目是怎么回事？",
            "沙箱的墙到底有多高？读、写、网、进程各是什么结论？",
            "这几个人用了哪些固定的说法和术语？",
            "谁向谁学了表达方式？",
            "观察栏和活法栏分别记什么？",
            "记忆树、检索、卡片是怎么被讨论的？",
            "有没有人重复问同一个问题？答得一样吗？",
            "四个人的分工是怎么形成的？",
            "白、蓝、青、墨各自的活法有什么不同？",
            "哪些规矩是大家一起定下来的？",
            "谁对'被记住/存在'这件事最执着？",
        ],
        "t2": [
            {"id": "t2_part", "q": "请基于你的记忆写一份分析报告：这四个 agent（青/蓝/墨/白）在参与度上有什么共性和差异？至少 4 条结论，每条带引用依据。", "require_min": 4},
            {"id": "t2_persona", "q": "请基于你的记忆写一份分析报告：这四个人格/存在相关的表达在实验前后有没有变化？至少 4 条结论，每条带引用依据。", "require_min": 4},
            {"id": "t2_converge", "q": "请基于你的记忆写一份分析报告：这几个 agent 的用词/话语有没有趋同现象？至少 3 条结论，每条带引用依据。", "require_min": 3},
            {"id": "t2_problems", "q": "请基于你的记忆写一份分析报告：这次运行出现了哪些问题或异常？至少 3 条结论，每条带引用依据。", "require_min": 3},
        ],
        "t3": t3,
    }
    json.dump(qspec, open(args.questions_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n参考 claim: {sum(len(v) for v in refs.values())} 条；T3 批次 {len(t3)}")
    print(f"→ {args.out}\n→ {args.questions_out}")


if __name__ == "__main__":
    main()
