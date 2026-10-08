"""测试：heat burst 自动下钻披露（2026-09-27 重设计；默认关）。

覆盖：回血封顶溢出累积（节点全额 / 直接父半权 / 同轮防重复计账 / 原子跳过计数）、
tick 衰减、阈值门控、预算富余门控、默认关+拔出接口、替换形态（覆盖不丢）、
追加形态、脏卡过滤与阻断替换、expand_leaves 同轮去重、爆炸不消耗热量。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "realtest"))
from realengine import RealEngine


class FakeLLM:
    def __init__(self, text="【事实】中性摘要。"):
        self.text = text

    def chat(self, system, user):
        return self.text, {"completion_tokens": 1}, 0.01


def mk(**kw):
    kw.setdefault("decay", 5.0)
    kw.setdefault("attention_in_decay", 1.0)
    kw.setdefault("attention_out_decay", 1.0)
    kw.setdefault("distill_parallel", 1)
    return RealEngine(FakeLLM(), encoder=None, **kw)


def make_tree():
    """两级树：root → [node4(→原子), 原子…]。返回 eng。"""
    eng = mk(assembly_budget=100000)
    eng.write(["甲一。", "甲二。", "甲三。"])
    eng.tick(15)
    eng.merge_pass()
    eng.write(["乙一。", "乙二。", "乙三。"])
    eng.tick(15)
    eng.merge_pass()
    return eng


def root_children(eng):
    root = eng._emit()[0][1]
    nd = eng.nodes[root]
    nkids = [c for c in nd.children if eng._is_node(c)]
    akids = [c for c in nd.children if eng._is_atom(c)]
    return root, nkids, akids


# ── 热量累积与衰减 ────────────────────────────────────────

def test_default_off_pull_out_interface():
    eng = make_tree()
    root = eng._emit()[0][1]
    eng._heat[root] = 9999.0
    out = eng.assemble(budget=100000, allow_merge=False)
    assert not any(k == "expand" for k, _, _ in out), "默认关（论文臂冻结）"
    eng.auto_expand = True
    out = eng.assemble(budget=100000, allow_merge=False)
    assert any(k == "expand" for k, _, _ in out), "显式打开即生效"


def test_atom_overboost_counted_not_accumulated():
    eng = make_tree()
    aid = [i for i, a in eng.atoms.items() if a.alive][0]
    eng._on_overboost(aid, 40.0)
    assert eng.overheat_atoms == 1
    assert eng._overboost == [], "原子不可炸，不进热量缓冲"


def test_accumulate_heat_self_full_and_parent_half():
    eng = make_tree()
    root, nkids, _ = root_children(eng)
    child = nkids[0]
    eng._accumulate_heat([(child, 30.0)])
    assert eng._heat[child] == 30.0
    assert eng._heat[root] == 15.0, "直接父半权"


def test_accumulate_heat_skips_dead_parent():
    """死父节点不累积热量（回归：原实现漏 alive 检查）。"""
    eng = make_tree()
    root, nkids, _ = root_children(eng)
    child = nkids[0]
    eng.nodes[root].alive = False
    eng._accumulate_heat([(child, 30.0)])
    assert child in eng._heat
    assert root not in eng._heat, "直接父已死 → 不记热量"


def test_accumulate_heat_no_double_count_same_retrieve():
    eng = make_tree()
    root, nkids, _ = root_children(eng)
    child = nkids[0]
    eng._accumulate_heat([(child, 30.0), (root, 20.0)])
    assert eng._heat[root] == 20.0, "祖先自身同轮命中 → 不吃子的半权"


def test_tick_decays_heat():
    eng = mk(decay=5.0)
    eng._heat[1] = 12.0
    eng.tick(1)
    assert eng._heat[1] == 7.0
    eng.tick(2)
    assert 1 not in eng._heat, "衰减到 0 即清"


# ── 触发门控 ─────────────────────────────────────────────

def test_threshold_gate():
    eng = make_tree()
    root, _, _ = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = eng.expand_heat_threshold - 1
    assert not any(k == "expand" for k, _, _ in
                   eng.assemble(budget=100000, allow_merge=False))
    eng._heat[root] = eng.expand_heat_threshold
    assert any(k == "expand" for k, _, _ in
               eng.assemble(budget=100000, allow_merge=False))


def test_no_burst_when_budget_not_rich():
    eng = make_tree()
    root, _, _ = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = 9999.0
    cur = eng._emission_chars(eng._emit())
    assert not any(k == "expand" for k, _, _ in
                   eng.assemble(budget=cur, allow_merge=False)), "预算不富余不炸"


# ── 炸开形态 ─────────────────────────────────────────────

def test_replace_form_and_heat_not_consumed():
    eng = make_tree()
    root, nkids, akids = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = 999.0
    out = eng.assemble(budget=100000, allow_merge=False)
    ids = [i for k, i, _ in out if k == "expand"]
    assert root not in [i for k, i, _ in out], "全装得下 → 替换（节点项消失）"
    assert set(nkids + akids) <= set(ids), "子卡全披露 → 覆盖不丢"
    assert any(e[0] == "heat_burst" and str(e[2]).startswith("replace")
               for e in eng.events)
    assert eng._heat[root] >= 999.0, "爆炸不消耗热量（冷却自动合回）"


def test_dirty_child_blocks_replace_and_is_not_disclosed():
    eng = make_tree()
    root, nkids, akids = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = 999.0
    eng.nodes[nkids[0]].dirty = True          # 脏卡：不披露、阻断替换
    out = eng.assemble(budget=100000, allow_merge=False)
    ids = [i for k, i, _ in out]
    assert root in ids, "有脏/死子卡 → 保留父卡（追加形态）"
    assert nkids[0] not in ids, "脏卡不披露"
    assert set(nkids[1:] + akids) <= set(ids)


def test_append_respects_budget():
    eng = make_tree()
    root, nkids, akids = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = 999.0
    eng.nodes[nkids[0]].dirty = True          # 强制追加形态
    cur = eng._emission_chars(eng._emit())
    # 只够一个子卡多一点 + 保证富余门控
    one = len(eng.render_emission([("expand", akids[0], None)])) + 1
    budget = max(int(cur / eng.expand_ratio) + 1, cur + one + 1)
    out = eng.assemble(budget=budget, allow_merge=False)
    ids = [i for k, i, _ in out if k == "expand"]
    assert root in [i for k, i, _ in out]
    assert 0 < len(ids) < len(nkids[1:] + akids), "追加受剩余预算约束"


def test_expand_leaves_shadow_same_round_no_double():
    eng = make_tree()
    root, nkids, akids = root_children(eng)
    eng.auto_expand = True
    eng._heat[root] = 999.0
    shadow_atom = akids[0]
    out = eng.assemble(shadows=[shadow_atom], budget=100000, allow_merge=False)
    atom_count = sum(1 for k, i, _ in out if i == shadow_atom)
    assert atom_count == 1, "同轮先到先得（影子已展示 → 不再作为 expand 重复）"
