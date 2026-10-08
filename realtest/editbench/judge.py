"""judge.py — LLM 判分（temp 0）。用于反转题与复述的语义判定，消确定性片段匹配的误伤。

三类判定：
- judge_pass(require, forbid, answer, llm)      ：反转/不变/远端题——答案是否语义满足 require 且
  不把 forbid 当当前事实陈述（改写可接受；"引用旧信息但明确标已删除/已改"不算违规）。
- judge_rot(forbid, answer, llm)                ：复述——把哪些旧事实当成了当前事实（上下文腐烂）。
- judge_retell(require, answer, llm)            ：复述——新内容要点的覆盖率（语义，非逐字）。

三级容错解析：直接 JSON → raw_decode 取首对象 → 正则抓 verdict/rot 字段。
"""
from __future__ import annotations

import json
import re

JUDGE_SYSTEM = "你是严格的事实裁判。只输出 JSON，不要解释。"


def _join(items):
    """checklist 项可为字符串，或**别名列表**（技术文档复述不回显标识符）。
    统一渲染成可供 judge 阅读的文本。"""
    out = []
    for x in (items or []):
        out.append(" / ".join(str(a) for a in x) if isinstance(x, list) else str(x))
    return "；".join(out)


def _parse_json(text):
    t = (text or "").strip()
    for attempt in (lambda s: json.loads(s),
                    lambda s: json.JSONDecoder().raw_decode(s)[0],
                    lambda s: _regex_parse(s)):
        try:
            return attempt(t)
        except Exception:
            continue
    return {}


def _regex_parse(t):
    m = re.search(r'["\'](verdict|rot|violated|hit)["\']\s*[:：]\s*["\']?([^"\',\]}]+)', t)
    if m:
        return {m.group(1): m.group(2).strip()}
    return {}


def judge_pass(require, forbid, answer, llm):
    """require 语义满足 + forbid 不当当前事实 → verdict=是。"""
    req = _join(require) or "（无）"
    fbd = _join(forbid) or "（无）"
    prompt = (
        "判断回答是否符合下列要求。\n"
        f"【必须陈述的要点】（改写、同义均可）：{req}\n"
        f"【不得当作当前事实陈述的旧内容】（若回答只是引用旧内容并明确标注"
        f"“已删除/已改/不再”，不算违规；只有把旧内容当成当前事实才算违规）：{fbd}\n"
        f"【回答】\n{answer}\n\n"
        '输出 JSON：{"verdict": "是|否", "reason": "一句话"}')
    text, _u, _l = llm.chat(JUDGE_SYSTEM, prompt)
    d = _parse_json(text)
    verdict = str(d.get("verdict", "否")).strip().lower()
    return {"pass": verdict.startswith("是") or verdict in ("yes", "true", "1"),
            "reason": d.get("reason", text[:80]), "raw": text[:200]}


def judge_rot(forbid, answer, llm):
    """返回复述中把旧事实当当前事实的违规项列表。"""
    fbd = _join(forbid)
    prompt = (
        "下面这份复述里，哪些下列【旧事实】被当成了当前事实陈述（即“上下文腐烂”）？"
        "若只是引用并明确标注“已删除/已改/不再”，不算违规。\n"
        f"【旧事实】{fbd}\n【复述】\n{answer}\n\n"
        '输出 JSON：{"rot": ["违规项1", ...], "reason": "一句话"}')
    text, _u, _l = llm.chat(JUDGE_SYSTEM, prompt)
    d = _parse_json(text)
    rot = d.get("rot") or d.get("violated") or []
    if not isinstance(rot, list):
        rot = [rot] if rot else []
    return rot


def judge_retell(require, answer, llm):
    """新内容要点的语义覆盖率：返回命中的 require 项列表。"""
    req = _join(require)
    prompt = (
        "判断下面这份复述是否覆盖了下列【要点】（改写、同义均可）。\n"
        f"【要点】{req}\n【复述】\n{answer}\n\n"
        '输出 JSON：{"hit": ["覆盖到的要点1", ...], "reason": "一句话"}')
    text, _u, _l = llm.chat(JUDGE_SYSTEM, prompt)
    d = _parse_json(text)
    hit = d.get("hit", [])
    if not isinstance(hit, list):
        hit = [hit] if hit else []
    return hit
