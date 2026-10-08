"""textutil.py — 文本工具单一真源（切词/归一/分位），供引擎与实验 harness 共用。

历史上这些函数在 6+ 个模块里各写一份且已漂移（归一规则不一致 → 去重/命中
判据静默不同）。此处收敛为两个明确命名的变体，语义显式：

- bigrams        ：仅去空白（引擎检索用；与 core/流水线一致）
- bigrams_strict ：去空白 + 非词字符（分析脚本用）
- norm_cjk       ：去空白 + 引号 + 中文标点（bench 比对用）
- norm_strict    ：去全部非词字符（ahi 归一用）
- quantiles      ：分位数摘要（n/mean/p10/p25/p50/p75/p90/低相似占比）
"""

import re


def bigrams(text):
    t = re.sub(r"\s+", "", text or "")
    if len(t) < 2:
        return set(t) if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def bigrams_strict(text):
    t = re.sub(r"[\s\W_]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def norm_cjk(s):
    return re.sub(r"[\s\"'“”‘’、，。！？]", "", s or "")


def norm_strict(t):
    return re.sub(r"[\s\W_]+", "", t or "")


def quantiles(vals):
    """分位数摘要；空输入 → {}。用于报告/分析，不影响机制。"""
    if not vals:
        return {}
    v = sorted(vals)

    def q(p):
        if len(v) == 1:
            return round(v[0], 4)
        k = (len(v) - 1) * p
        f, c = int(k), min(len(v) - 1, int(k) + 1)
        return round(v[f] + (v[c] - v[f]) * (k - f), 4)

    return {"n": len(v), "mean": round(sum(v) / len(v), 4),
            "p10": q(.10), "p25": q(.25), "p50": q(.50), "p75": q(.75),
            "p90": q(.90),
            "frac_lt_0.4": round(sum(1 for x in v if x < 0.4) / len(v), 4),
            "frac_lt_0.5": round(sum(1 for x in v if x < 0.5) / len(v), 4)}
