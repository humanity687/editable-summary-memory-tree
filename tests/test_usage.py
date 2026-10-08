"""测试：使用信号层（realtest/usage.py）——切句/分档/记录/影子模式不变量。

设计规格：realtest/USAGE_SPEC.md §6（Phase B：影子模式只记不读，零 LLM 纯计算）。
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "realtest"))
from realengine import RealEngine
from realtest.usage import (
    UsageLayer, split_units, parse_cite_ids, tree_related,
    nearest_screen_ancestor, intersection_bring,
)


class FakeLLM:
    def __init__(self):
        self.model = "fake"

    def chat(self, a, b):
        return "摘要。", {"prompt_tokens": 1, "completion_tokens": 1}, 0.01


class FakeEnc:
    def encode(self, text, normalize_embeddings=True, batch_size=None):
        single = isinstance(text, str)
        xs = [text] if single else list(text)
        out = []
        for t in xs:
            v = np.zeros(64)
            for ch in t[:64]:
                v[ord(ch) % 64] += 1
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return out[0] if single else np.array(out)


def make_engine():
    eng = RealEngine(FakeLLM(), encoder=FakeEnc(), assembly_budget=10000,
                     attention_in_decay=1.0, attention_out_decay=1.0)
    eng.write(["小明穿了一件红色棉袄，然后出了门。",
               "小红在院子里喂鸡。",
               "小明出门发现有点冷，回来找了一件红色棉袄穿。"])
    eng.tick(15)
    eng.merge_pass()
    return eng


def snapshot(eng):
    return {
        "atoms": {i: (a.value, a.alive, round(a.hp, 4), a.owner)
                  for i, a in eng.atoms.items()},
        "nodes": {i: (n.value, n.alive, round(n.hp, 4), n.dirty, tuple(n.children))
                  for i, n in eng.nodes.items()},
        "tiling": list(eng.tiling), "round": eng.round, "events": len(eng.events),
    }


def alive_ids(eng):
    return [i for i, a in eng.atoms.items() if a.alive]


# ── 纯函数：切句 / 引用解析 / 亲缘 ─────────────────────────

def test_split_units_protects_citation_and_code_fence():
    text = ('甲说过 (依据:#N12 "乙。丙；丁") 一句。下一句。\n'
            '```py\nprint("x。y")\n```\n尾句。')
    units = split_units(text)
    assert any('依据:#N12 "乙。丙；丁"' in u for u in units)
    assert any('print("x。y")' in u for u in units)
    assert not any(u == "乙。" for u in units)


def test_parse_cite_ids_variants():
    assert parse_cite_ids('(依据:#N6039 "x") (依据:N4060 "y") (依据:#9804 "z")') \
        == [6039, 4060, 9804]
    assert parse_cite_ids("AN123 与 BN456 不算引用") == []
    assert parse_cite_ids("") == []


def test_tree_related_ancestor():
    eng = make_engine()
    pairs = [(i, a.owner) for i, a in eng.atoms.items() if a.alive and a.owner]
    if not pairs:
        eng.tick(20)
        eng.merge_pass()
        pairs = [(i, a.owner) for i, a in eng.atoms.items() if a.alive and a.owner]
    assert pairs
    aid, owner = pairs[0]
    assert tree_related(eng, aid, owner)
    assert tree_related(eng, owner, aid)
    others = [i for i in alive_ids(eng) if i != aid]
    if others:
        assert not tree_related(eng, aid, others[0]) or eng.atoms[others[0]].owner == owner


def test_coverage_semantics_resolution():
    eng = make_engine()
    pairs = [(i, a.owner) for i, a in eng.atoms.items() if a.alive and a.owner]
    assert pairs
    aid, owner = pairs[0]
    pos = {item: idx for idx, item in enumerate(eng.chain_ids())}
    cov = nearest_screen_ancestor(eng, aid, {owner}, pos)
    assert cov["self"] is False and cov["cover_span"] >= 1
    assert nearest_screen_ancestor(eng, aid, {aid}, pos)["self"] is True


# ── UsageLayer：记录 / 影子模式 / 过滤 ─────────────────────

def test_record_t1_edges_and_shadow_invariant():
    eng = make_engine()
    before = snapshot(eng)
    aid = alive_ids(eng)[0]
    layer = UsageLayer(eng, params={"match_score_floor": 0.99})
    stats = layer.record_output(
        [(f'我的依据是 (依据:#{aid} "原文摘录")', aid + 1000)],
        injected_ids=set(eng.tiling) | {aid})
    assert stats["units_t1"] == 1 and stats["edges_t1"] == 1
    assert (aid + 1000, aid) in layer.edges
    assert snapshot(eng) == before, "影子模式不得修改引擎状态"
    layer.close()


def test_reread_skipped():
    eng = make_engine()
    aid = alive_ids(eng)[0]
    layer = UsageLayer(eng, params={"match_score_floor": 0.0})
    stats = layer.record_output([(eng.atoms[aid].value, 9999)],
                                injected_ids={aid})
    assert stats["units_reread"] == 1
    assert not layer.edges
    layer.close()


def test_duplicate_s_ref_skipped():
    eng = make_engine()
    aid = alive_ids(eng)[0]
    layer = UsageLayer(eng, params={"match_score_floor": 0.99})
    layer.record_output([(f'(依据:#{aid} "x")', 42)], injected_ids=set(eng.tiling))
    edges = dict(layer.edges)
    layer.record_output([(f'(依据:#{aid} "y")', 42)], injected_ids=set(eng.tiling))
    assert layer.edges == edges
    layer.close()


def test_tree_related_pairs_excluded():
    eng = make_engine()
    pairs = [(i, a.owner) for i, a in eng.atoms.items() if a.alive and a.owner]
    assert pairs
    aid, owner = pairs[0]
    layer = UsageLayer(eng, params={"theta_nonredundant": 1.1})
    layer.record_output(
        [(f'(依据:#{aid} "x") (依据:N{owner} "y")', 7777)],
        injected_ids=set(eng.tiling) | {aid, owner})
    key = (min(aid, owner), max(aid, owner))
    assert key not in layer.pairs, "树亲缘对（祖先-后代）不得记为派生边"
    assert len(layer.edges) == 2  # S→A 仍全记（可审计）
    layer.close()


def test_nonredundant_pair_recorded():
    eng = make_engine()
    ids = alive_ids(eng)[:2]
    assert len(ids) == 2 and not tree_related(eng, ids[0], ids[1])
    layer = UsageLayer(eng, params={"theta_nonredundant": 1.1})
    layer.record_output(
        [(f'(依据:#{ids[0]} "x") (依据:#{ids[1]} "y")', 555)],
        injected_ids=set(eng.tiling))
    assert layer.pairs, "非冗余（此处阈值放宽）同句对应被记录"
    layer.close()


def test_intersection_bring_weights_and_exclusions():
    pairs = {
        (1, 2): {"weight": 2.0},
        (1, 3): {"weight": 5.0},
        (2, 3): {"weight": 1.0},
        (2, 4): {"weight": 9.0},
        (8, 9): {"weight": 100.0},   # 与锚无关，不得带入
    }
    out = intersection_bring([1, 2], pairs, topk=3)
    assert out == [4, 3]             # 权重降序；锚 1/2 排除
    out2 = intersection_bring([1, 2], pairs, topk=3, exclude={4})
    assert out2 == [3]
    assert intersection_bring([], pairs) == []


def test_usage_layer_bring_delegates():
    eng = make_engine()
    layer = UsageLayer(eng)
    layer.pairs = {(min(eng.atoms), max(eng.atoms)): {"weight": 1.0}}
    a, b = list(layer.pairs)[0]
    assert layer.bring([a]) == [b]
    assert layer.bring([a], exclude={b}) == []
    layer.close()


def test_units_total_counts_deduped_units():
    """units_total 与分档计数同口径：同一 s_ref 只记一次（回归）。"""
    eng = make_engine()
    aid = alive_ids(eng)[0]
    layer = UsageLayer(eng)
    st = layer.record_output([("甲句。", aid), ("乙句。", aid)],
                             injected_ids=set(eng.tiling))
    assert st["units_total"] == 1
    layer.close()


def test_output_jsonl_written(tmp_path=None):
    import tempfile
    eng = make_engine()
    aid = alive_ids(eng)[0]
    with tempfile.TemporaryDirectory() as d:
        layer = UsageLayer(eng, out_dir=d)
        layer.record_output([(f'(依据:#{aid} "x")', None)], injected_ids=set(eng.tiling))
        layer.close()
        out = os.path.join(d, "usage_outputs.jsonl")
        assert os.path.exists(out)
        assert os.path.exists(os.path.join(d, "usage_edges.jsonl"))
        assert os.path.getsize(out) > 0, "审计流必须非空（flush 回归）"
