"""score.py — 编辑风暴实验评分（smoke：全确定性，零 LLM；judge 留接口后续接）。

- Q/A：按题目的 check_before / check_after 判定（require 必含片段 / forbid 禁含片段）。
  require 全中且 forbid 全不中 → pass（与 bench_qa.score_answer 同口径，norm_cjk 归一）。
  反转题 phase=after 即"反转正确率"（旧信息污染的直接量化）；
  delete 题用 forbid（被删内容不应出现）= 旧答案出现率的反向指标。
- 复述：两层——自动 checklist（旧内容残留率 = rot，新内容覆盖率 = coverage）全量跑；
  LLM judge 只跑延续臂（后续接入，smoke 不启用）。
"""
from __future__ import annotations

import re

from textutil import norm_cjk as norm

# 句级匹配（2026-10-01）：norm_cjk 去标点会把相邻句粘在一起，导致跨句拼接误匹配
# （如 forbid「四面墙，四个高度」被「…四面墙。四个高度…」命中）。先按句末标点/换行
# 切段，再在**单句内**做 require/forbid 子串判定。
_SENT_SPLIT = re.compile(r"[。！？!?…；;\n]+")


def _in_any_sentence(text, frag):
    f = norm(frag)
    if not f:
        return False
    return any(f in norm(seg) for seg in _SENT_SPLIT.split(text or ""))


def check_spec(text, spec):
    if not spec:
        return True, {"require": [], "forbid": []}
    req = spec.get("require", [])
    forb = spec.get("forbid", [])
    missed = [f for f in req if not _in_any_sentence(text, f)]
    leaked = [f for f in forb if _in_any_sentence(text, f)]
    ok = (not missed) and (not leaked)
    return ok, {"require_miss": missed, "forbid_hit": leaked}


def score_qa(answer, item, phase):
    # 回退链：本阶段 spec → 另一阶段 spec → 空。不变/远端题只有 check_before，
    # phase=after 时必须回退到 check_before（否则空 spec 恒 pass，虚高通过率）。
    spec = (item.get(f"check_{phase}") or item.get("check_after")
            or item.get("check_before") or {})
    ok, detail = check_spec(answer, spec)
    return {"id": item["id"], "layer": item.get("layer", "?"),
            "q": item["q"], "pass": bool(ok), "detail": detail}


def _item_hit(t, item):
    """一个 checklist 项命中：item 可为字符串，或**别名列表**（任一 aliases 子串命中即可）。
    别名列表用于容纳『标识符 / 自然语言短语 / 关键值』多种写法（技术文档复述不回显标识符）。"""
    alts = item if isinstance(item, list) else [item]
    return any(norm(a) and norm(a) in t for a in alts)


def _label(item):
    return "/".join(item) if isinstance(item, list) else item


def score_retell(answer, checklist):
    t = norm(answer or "")
    req = checklist.get("require", [])
    forb = checklist.get("forbid", [])
    req_hit = [f for f in req if _item_hit(t, f)]
    rot_hit = [f for f in forb if _item_hit(t, f)]

    def _cov(items):
        items = items or []
        return (round(sum(1 for f in items if _item_hit(t, f)) / len(items), 3)
                if items else None)

    return {
        "coverage": f"{len(req_hit)}/{len(req)}",
        "coverage_ratio": round(len(req_hit) / len(req), 3) if req else 0.0,
        # 覆盖率两段（预注册：在 warm 提问记录中的关键词 vs 不在的）
        "coverage_record": _cov(checklist.get("require_record")),
        "coverage_nonrecord": _cov(checklist.get("require_nonrecord")),
        "rot": f"{len(rot_hit)}/{len(forb)}",
        "rot_ratio": round(len(rot_hit) / len(forb), 3) if forb else 0.0,
        "new_miss": [_label(f) for f in req if not _item_hit(t, f)],
        "old_leak": [_label(f) for f in rot_hit],
        "chars": len(answer or ""),
    }


def summarize(phase_results):
    """phase_results: {arm: [score_qa, ...]} → 每臂 pass 率 + 分层。"""
    out = {}
    for arm, rows in phase_results.items():
        n = len(rows)
        passed = sum(1 for r in rows if r["pass"])
        layers = {}
        for r in rows:
            d = layers.setdefault(r["layer"], [0, 0])
            d[1] += 1
            d[0] += 1 if r["pass"] else 0
        out[arm] = {"n": n, "pass": passed,
                    "pass_rate": round(passed / n, 3) if n else 0.0,
                    "layers": {k: f"{v[0]}/{v[1]}" for k, v in layers.items()}}
    return out
