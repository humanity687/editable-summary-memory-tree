"""测试 H：随机模糊（固定种子）+ 全不变量。

每步后断言：
I1 链序 == 镜像
I2 发射覆盖 == 镜像（无重叠、无缝隙、按位置序）
I3 干净节点 value == oracle（O(n) 后序）
I4 脏集合精确 == 受影响原子的 owner 链并集（随步累积，pass 清空）
I5 组装/检索结果不含脏节点
I6 铺陈项均为顶层且存活
"""

import random

import pytest

from core import Engine
from util import mk_engine


def checks(e, mirror_ids, mirror_vals, dirty_expected):
    assert e.chain_ids() == mirror_ids
    assert [e.atoms[a].value for a in e.chain_ids()] == mirror_vals
    emitted = e._emit()
    assert e.emission_atoms(emitted) == mirror_ids
    for kind, item, _ in emitted:
        if kind == "node":
            assert not e.nodes[item].dirty
    ors = e.oracle_all()
    for nid, nd in e.nodes.items():
        if nd.alive and not nd.dirty:
            if nd.placeholder:
                assert nd.value is None, f"placeholder node {nid} must have value None"
            else:
                exp = ors.get(nid, (None, 0))[0]
                if exp is None:
                    assert nd.value is None, f"node {nid} value should be None"
                else:
                    assert abs(nd.value - exp) < 1e-9, f"node {nid} value stale"
    actual = {nid for nid, nd in e.nodes.items() if nd.alive and nd.dirty}
    assert actual == dirty_expected, f"dirty mismatch: {actual} vs {dirty_expected}"
    # I7 子区间恰好拼满父区间（§3.8.5）：干净非占位节点的子 span 串接 == 自身 span
    for nid, nd in e.nodes.items():
        if not (nd.alive and not nd.dirty and not nd.placeholder):
            continue
        concat = []
        for c in nd.children:
            if e._is_atom(c):
                if e.atoms[c].alive:
                    concat.append(c)
            elif e.nodes[c].alive:
                concat.extend(e.node_atoms(c))
        assert concat == e.node_atoms(nid), f"node {nid} children span != own span"
    for item in e.tiling:
        if e._is_atom(item):
            assert e.atoms[item].alive and e.atoms[item].owner is None
        else:
            assert e.nodes[item].alive and e.nodes[item].parent is None
            assert item not in e.nodes[item].children


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_fuzz(seed):
    rng = random.Random(seed)
    e = mk_engine(decay=1.0, hp_merge_threshold=30.0, parent_init_hp=70.0,
               hot_threshold=60.0, merge_cap_atoms=40)
    mirror_ids = []
    mirror_vals = []
    dirty_expected = set()
    for _ in range(rng.randint(1, 3)):
        vals = [rng.randint(0, 100) for _ in range(rng.randint(1, 6))]
        mirror_ids.extend(e.write(vals))
        mirror_vals.extend(vals)

    def chains_of(ids):
        s = set()
        for aid in ids:
            s.update(e._owner_chain(aid))
        return s

    for step in range(2000):
        if len(mirror_ids) > 500:
            e.tick(20)
            e.merge_pass()
            dirty_expected = set()
        op = rng.choice(["write", "insert", "delete", "replace", "tick", "pass", "assemble", "retrieve", "promote"])
        if op == "write":
            vals = [rng.randint(0, 100) for _ in range(rng.randint(1, 5))]
            new = e.write(vals)
            mirror_ids.extend(new)
            mirror_vals.extend(vals)
            dirty_expected |= chains_of(new)
        elif op == "insert":
            if not mirror_ids:
                continue
            idx = rng.randint(0, len(mirror_ids))
            left = mirror_ids[idx - 1] if idx > 0 else None
            right = mirror_ids[idx] if idx < len(mirror_ids) else None
            vals = [rng.randint(0, 100) for _ in range(rng.randint(1, 4))]
            new = e.insert(left, right, vals)
            mirror_ids[idx:idx] = new
            mirror_vals[idx:idx] = vals
            dirty_expected |= chains_of(new)
        elif op == "delete":
            if not mirror_ids:
                continue
            i = rng.randint(0, len(mirror_ids) - 1)
            j = min(len(mirror_ids) - 1, i + rng.randint(0, 4))
            dead = mirror_ids[i:j + 1]
            e.delete(dead)
            del mirror_ids[i:j + 1]
            del mirror_vals[i:j + 1]
            dirty_expected |= chains_of(dead)
        elif op == "replace":
            if not mirror_ids:
                continue
            i = rng.randint(0, len(mirror_ids) - 1)
            j = min(len(mirror_ids) - 1, i + rng.randint(0, 4))
            old = mirror_ids[i:j + 1]
            vals = [rng.randint(0, 100) for _ in range(rng.randint(1, 4))]
            new = e.replace(old, vals)
            mirror_ids[i:j + 1] = new
            mirror_vals[i:j + 1] = vals
            dirty_expected |= chains_of(old) | chains_of(new)
        elif op == "tick":
            e.tick(rng.randint(1, 3))
        elif op == "pass":
            e.merge_pass()
            dirty_expected = set()
        elif op == "assemble":
            out = e.assemble(budget=100000)
            assert e.emission_atoms(out) == mirror_ids
            for kind, item, _ in out:
                if kind == "node":
                    assert not e.nodes[item].dirty
        elif op == "retrieve":
            q = rng.randint(0, 100)
            budget = rng.randint(1, 8)
            res = e.retrieve(q, budget=budget)
            assert all(not e.nodes[nid].dirty for nid in res)
            assert len(res) == len(set(res))
            assert len(res) <= budget + 64
            dirty_expected = {nid for nid, nd in e.nodes.items() if nd.alive and nd.dirty}
        elif op == "promote":
            candidates = [nid for nid, nd in e.nodes.items() if nd.alive and nd.parent is not None]
            if candidates:
                e.promote(rng.choice(candidates))
                dirty_expected = {nid for nid, nd in e.nodes.items() if nd.alive and nd.dirty}
        checks(e, mirror_ids, mirror_vals, dirty_expected)
        if step % 500 == 499:
            e.merge_pass()
            dirty_expected = set()
            assert not any(nd.dirty for nd in e.nodes.values() if nd.alive)
            checks(e, mirror_ids, mirror_vals, dirty_expected)
    e.merge_pass()
    dirty_expected = set()
    checks(e, mirror_ids, mirror_vals, dirty_expected)


@pytest.mark.parametrize("seed", [3, 4, 5])
def test_rollback_symmetry_fuzz(seed):
    """§8.1.5：任意单次编辑 + 回滚 = 原状态（链序与内容级断言）。"""
    rng = random.Random(seed)
    e = mk_engine(decay=1.0, hp_merge_threshold=30.0, merge_cap_atoms=40)
    for _ in range(6):
        e.write([rng.randint(0, 100) for _ in range(rng.randint(1, 5))])
    e.tick(20)
    e.merge_pass()
    for _ in range(300):
        ids = e.chain_ids()
        snap_ids = list(ids)
        snap_vals = [e.atoms[a].value for a in ids]
        op = rng.choice(["write", "insert", "delete", "replace"])
        steps = 1
        if op == "write":
            e.write([rng.randint(0, 100) for _ in range(rng.randint(1, 4))])
        elif op == "insert" and ids:
            idx = rng.randint(0, len(ids))
            left = ids[idx - 1] if idx > 0 else None
            right = ids[idx] if idx < len(ids) else None
            e.insert(left, right, [rng.randint(0, 100) for _ in range(rng.randint(1, 3))])
        elif op == "delete" and ids:
            i = rng.randint(0, len(ids) - 1)
            j = min(len(ids) - 1, i + rng.randint(0, 3))
            e.delete(ids[i:j + 1])
        elif op == "replace" and ids:
            i = rng.randint(0, len(ids) - 1)
            j = min(len(ids) - 1, i + rng.randint(0, 3))
            e.replace(ids[i:j + 1], [rng.randint(0, 100) for _ in range(rng.randint(1, 3))])
            steps = 2   # replace = delete + insert 两条增量
        else:
            continue
        e.rollback(steps)
        assert e.chain_ids() == snap_ids, f"seed{seed} rollback 链序不一致（{op}）"
        assert [e.atoms[a].value for a in e.chain_ids()] == snap_vals, \
            f"seed{seed} rollback 内容不一致（{op}）"
