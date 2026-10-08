"""测试 C：组装（clean tiling）/ 预算裁剪 / 盲区。"""

from core import Engine
from util import mk_engine, single_node_book


def test_node_atoms_canonical_traversal():
    """node_atoms 是"节点→区间原子"唯一入口：存活节点给位置序全原子；死/不存在 → None。"""
    e, ids, n1 = single_node_book(5)
    assert e.node_atoms(n1) == ids
    assert e.node_atoms(999999) is None
    e.nodes[n1].alive = False
    assert e.node_atoms(n1) is None


def test_emission_chain_order():
    e, ids, n1 = single_node_book(10)
    emitted = e._emit()
    assert emitted == [("node", n1, e.nodes[n1].value)]
    assert e.emission_atoms(emitted) == e.chain_ids()


def test_emission_with_hot_zone():
    e, ids, n1 = single_node_book(10)
    b = e.write([50.0, 51.0])
    emitted = e._emit()
    assert emitted == [("node", n1, e.nodes[n1].value),
                       ("atom", b[0], 50.0), ("atom", b[1], 51.0)]
    assert e.emission_atoms(emitted) == e.chain_ids()


def test_dirty_node_expands_to_children_in_order():
    e = mk_engine(decay=5.0)
    a = e.write([float(i) for i in range(10)])
    e.tick(15)
    e.merge_pass()
    b = e.write([float(i) for i in range(10, 20)])
    e.tick(10)
    e.merge_pass()
    e.tick(5)
    e.merge_pass()
    p = e.tiling[0]
    e.delete([a[2]])
    emitted = e._emit()
    assert e.emission_atoms(emitted) == e.chain_ids()
    assert all(k == "atom" for k, _, _ in emitted)
    e.merge_pass()
    emitted = e._emit()
    assert emitted == [("node", p, e.nodes[p].value)]
    assert e.emission_atoms(emitted) == e.chain_ids()


def test_edit_region_peeled_view():
    e, ids, n1 = single_node_book(10)
    new = e.insert(ids[4], ids[5], [99.0, 98.0])
    emitted = e._emit()
    assert e.emission_atoms(emitted) == e.chain_ids()
    assert all(k == "atom" for k, _, _ in emitted)
    e.merge_pass()
    assert e.nodes[n1].placeholder
    # 透明占位（默认）：递归展开子卡，绝不发射【占位】节点，覆盖无缝隙
    emitted = e._emit()
    assert all(k == "atom" for k, _, _ in emitted)
    assert e.emission_atoms(emitted) == e.chain_ids()
    # 非透明（旧行为，供 A/B 对照）：发射占位节点标记
    e2, ids2, n2 = single_node_book(10)
    e2.placeholder_transparent = False
    e2.insert(ids2[4], ids2[5], [99.0, 98.0])
    e2.merge_pass()
    assert e2._emit() == [("node", n2, None)]


def test_budget_overflow_triggers_merge():
    e = mk_engine(decay=5.0, merge_fanout_cap=50)
    e.write([float(i) for i in range(20)])
    e.tick(15)
    out = e.assemble(budget=10)
    assert len(out) == 1
    assert out[0][0] == "node"
    assert e.emission_atoms(out) == e.chain_ids()


def test_trim_drops_nodes_keeps_atoms_then_hard_trim():
    e = mk_engine(decay=5.0)
    e.write([float(i) for i in range(5)])
    e.tick(15)
    e.merge_pass()
    e.write([100.0 + i for i in range(5)])
    out = e.assemble(budget=4)
    assert len(out) == 4
    assert all(k == "atom" for k, _, _ in out)
    assert any(k[0] == "hard_trim" for k in e.events)


def test_trim_prefers_low_hp_nodes():
    e = mk_engine(decay=5.0)
    e.write([1.0, 2.0, 3.0])
    e.tick(15)
    e.merge_pass()
    b = e.write([50.0, 51.0])
    e.tick(9)
    e.merge_pass()
    e.tick(6)
    e.merge_pass()
    n2 = e.tiling[0]
    e.write([100.0, 101.0])
    out = e.assemble(budget=3)
    assert len(out) == 3
    kinds = [k for k, _, _ in out]
    assert "node" in kinds
    for k, item, _ in out:
        if k == "node":
            assert not e.nodes[item].dirty


def test_assembly_never_emits_dirty_node():
    e, ids, n1 = single_node_book(10)
    e.delete([ids[0]])
    for budget in (100, 3):
        out = e.assemble(budget=budget)
        for k, item, _ in out:
            if k == "node":
                assert not e.nodes[item].dirty


def test_placeholder_transparent_and_opt_out():
    """占位卡透明（默认）：渲染递归子卡、不发射占位标记；关闭开关恢复旧行为。"""
    e, ids, n1 = single_node_book(10)
    e.insert(ids[4], ids[5], [50.0])
    e.merge_pass()
    assert e.nodes[n1].placeholder
    emitted = e._emit()
    assert not any(k == "node" and item == n1 for k, item, _ in emitted)
    assert e.emission_atoms(emitted) == e.chain_ids()
    # opt-out（旧行为）
    e.placeholder_transparent = False
    assert e._emit() == [("node", n1, None)]


def test_placeholder_excluded_from_mechanisms():
    """透明占位卡=空气：不参与 merge 分组、不占注意力节点名额（按 decay 而非 in_decay 衰减）。"""
    e = mk_engine(decay=5.0)
    e.write([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    e.tick(15)
    e.merge_pass()
    n = e.tiling[0]
    chain = e.chain_ids()
    e.insert(chain[4], chain[5], [50.0])
    e.merge_pass()
    assert e.nodes[n].placeholder
    # merge 候选不含占位卡
    e.nodes[n].hp = 0.0
    flat = [x for r in e._find_merge_runs() for x in r]
    assert n not in flat
    # 注意力：占位卡不入节点窗口 → 按 decay 衰减（而非 in_decay）
    e.nodes[n].hp = 100.0
    e.tick(1)
    assert abs(e.nodes[n].hp - (100.0 - e.decay)) < 1e-6
