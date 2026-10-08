"""测试：self_quota 实验操控（组装层"自我可见性地板"，默认关）。

覆盖：
- 默认关（ratio=0）零注入、但仍记录真实 self 占比；
- ratio>0 时把 source=="ai" 原话补到目标占比，且不超预算；
- 不双注（已在视野的自身原子不重复注入）；
- 挤占序只裁非自我旧原子，自身原话与本轮新原子受保护；
- 渲染成正常自身发言段（clean）而非"召回"标注。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "realtest"))
from realengine import RealEngine  # noqa: E402


class FakeLLM:
    def __init__(self, text="【事实】中性摘要。"):
        self.text = text
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return self.text, {"prompt_tokens": 1, "completion_tokens": 1}, 0.0


def mk(**kw):
    kw.setdefault("decay", 5.0)
    kw.setdefault("attention_in_decay", 1.0)
    kw.setdefault("attention_out_decay", 1.0)
    kw.setdefault("distill_parallel", 1)
    return RealEngine(FakeLLM(), encoder=None, **kw)


def add(eng, texts, source):
    ids = eng.write(texts)
    for i in ids:
        eng.atoms[i].metadata["source"] = source
    return ids


def test_off_by_default_no_injection_but_ratio_recorded():
    eng = mk(assembly_budget=100000)
    add(eng, ["用户消息一句。"] * 5, "user:experimenter")
    add(eng, ["我自己的话一句。"] * 3, "ai")
    out = eng.assemble(budget=100000)
    assert not any(k == "self_quota" for k, _, _ in out)
    assert 0.0 < eng._last_self_ratio < 1.0


def test_quota_raises_self_ratio_and_respects_budget():
    eng = mk(assembly_budget=100000)
    # 自身原话写在头部（trim 头砍 + HP 并列时先裁），非自我写尾部 → trim 后自身被挤出
    add(eng, ["我自己的第一句原话，需要被保留在视野里并延续风格。",
              "我自己的第二句原话，继续保持我自己的用词与语气。",
              "我自己的第三句原话，作为稳定的自我信号存在。",
              "我自己的第四句原话，避免人格向群体均值漂移。",
              "我自己的第五句原话，让自我历史保持可见性。",
              "我自己的第六句原话，作为自我模仿的素材。"], "ai")
    for _ in range(20):
        add(eng, ["实验员的一条比较长的新消息内容，用于挤占组装预算空间。"],
            "user:experimenter")
    eng.tick(3)
    eng.self_quota_ratio = 0.4
    out = eng.assemble(budget=300)
    assert any(k == "self_quota" for k, _, _ in out)
    assert eng._emission_chars(out) <= 300
    assert eng._last_self_ratio >= 0.35


def test_no_double_inject_when_already_visible():
    eng = mk(assembly_budget=100000)
    for _ in range(10):
        add(eng, ["用户消息内容。"] * 1, "user:experimenter")
    sid = add(eng, ["自身原话，已经在视野中。"], "ai")[0]
    eng.self_quota_ratio = 0.9   # 高目标，逼注入
    out = eng.assemble(budget=100000)
    assert sum(1 for _k, it, _v in out if it == sid) == 1


def test_evict_for_quota_drops_non_self_only():
    eng = mk(assembly_budget=100000)
    u1 = add(eng, ["用户旧消息一句，占位。"] * 1, "user:experimenter")[0]
    s1 = add(eng, ["我的自身原话一句，必须保留。"] * 1, "ai")[0]
    eng.tick(2)   # round=2 → created_round(0) < round → 旧原子可裁
    out = [("atom", u1, eng.atoms[u1].value), ("atom", s1, eng.atoms[s1].value)]
    freed = eng._evict_for_quota(out, need=1)
    ids = {it for _k, it, _v in out}
    assert freed > 0
    assert s1 in ids and u1 not in ids


def test_evict_protects_current_round_atoms():
    eng = mk(assembly_budget=100000)
    u = add(eng, ["本轮用户消息，不能被裁。"], "user:experimenter")[0]
    # round 仍 0 → created_round == round → 不可裁
    out = [("atom", u, eng.atoms[u].value)]
    freed = eng._evict_for_quota(out, need=999)
    assert freed == 0
    assert u in {it for _k, it, _v in out}


def test_render_clean_self_quota_as_own_segment_not_recall():
    eng = mk(assembly_budget=100000, render_clean=True)
    sid = add(eng, ["这是被注入的自身原话，按正常发言渲染。"], "ai")[0]
    eng.source_labels = {"ai": "林深"}
    out = eng.render_emission([("self_quota", sid, eng.atoms[sid].value)])
    assert "▸〔林深〕" in out
    assert "这是被注入的自身原话" in out
    assert "召回" not in out


def test_quota_off_render_unchanged():
    """ratio=0 时 assemble 输出与不调用 quota 一致（零行为干预）。"""
    eng = mk(assembly_budget=100000)
    add(eng, ["甲。"] * 4, "user:experimenter")
    add(eng, ["乙。"] * 2, "ai")
    base = eng._emit()
    out = eng._apply_self_quota(base, 100000)
    assert out == base


def test_self_ratio_never_exceeds_one():
    """回归（2026-10-04）：逐条隔离渲染重复计段头 → 占比可 >1（实测 1.014/1.22）。

    全部自身原话、同段折叠时，占比应为 1.0 而非 >1（段头只应计一次）。"""
    eng = mk(assembly_budget=100000)
    add(eng, ["我自己的话第一句内容较长一些。",
              "我自己的话第二句内容较长一些。",
              "我自己的话第三句内容较长一些。",
              "我自己的话第四句内容较长一些。",
              "我自己的话第五句内容较长一些。"], "ai")
    out = eng._apply_self_quota(eng._emit(), 100000)
    assert eng._last_self_ratio <= 1.0 + 1e-9
    assert eng._last_self_ratio > 0.9


def test_self_ratio_segment_accounting_matches_render():
    """自身原话按段聚合：占比落在 (0,1)，且自身越多占比越高（单调）。"""
    eng = mk(assembly_budget=100000)
    add(eng, ["用户的一条消息，内容长度中等。"] * 4, "user:experimenter")
    add(eng, ["自己的一条消息，内容长度中等。"], "ai")
    eng._apply_self_quota(eng._emit(), 100000)
    r1 = eng._last_self_ratio
    add(eng, ["自己的又一条消息，内容长度中等。"] * 3, "ai")
    eng._apply_self_quota(eng._emit(), 100000)
    r2 = eng._last_self_ratio
    assert 0.0 < r1 < r2 < 1.0
    assert eng._self_rendered_chars(eng._emit()) <= eng._emission_chars(eng._emit())
