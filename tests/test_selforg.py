"""测试：使用信号 × cooc 主题层协同（USAGE_SPEC 协同章，2026-09-26 拍定）。

覆盖：跨查询配对/θ 过滤/无会话零边 → 使用事件汇入（边/证据/触发输出/demand）→
N=6 触发 → 成员序（证据优先、T3 不入分）→ bring 交集/补齐/via → 概念 prompt →
UsageLayer 事件契约与 ConceptEngine(selforg) 装配。
"""

import io
import json
import os
import sys
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "realtest"))
from realengine import RealEngine
from realtest.cooc import ConceptEngine
from realtest.usage import UsageLayer


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, system, user):
        self.prompts.append(user)
        return "【标题】测试主题\n【概括】测试概括。", {"completion_tokens": 1}, 0.01


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


TEXTS = [
    "小明穿了一件红色棉袄出门。",
    "小红在院子里喂鸡。",
    "井口被石头封住了。",
    "地窖里藏着半袋米。",
    "老槐树下埋着一只铜锁。",
    "纸上写着三行小字。",
    "河边停着一艘乌篷船。",
    "屋顶上晾着两张渔网。",
]


def make_eng(cooc_params=None, selforg=False):
    llm = FakeLLM()
    eng = ConceptEngine(llm, encoder=FakeEnc(), assembly_budget=10000,
                        attention_in_decay=1.0, attention_out_decay=1.0,
                        cooc_params=dict(cooc_params or {}), selforg=selforg)
    return eng, llm


def make_tree_eng(cooc_params=None, selforg=False):
    eng, llm = make_eng(cooc_params, selforg)
    eng.write(TEXTS)
    eng.tick(15)
    eng.merge_pass()
    return eng, llm


# ── 跨查询召回边（叉口 1）────────────────────────────────

def test_same_query_no_pairs():
    eng, _ = make_tree_eng()
    c = eng.cooc
    c.begin_session("s1")
    c.log_recall("同一问题", [1, 2], {})
    c.log_recall("同一问题", [1, 2], {})
    assert len(c.edges) == 0, "同查询命中集内不配对（相似度冗余）"


def test_cross_query_pairs_only():
    eng, _ = make_tree_eng({"theta_nr": 1.1})   # 关相似度闸，单测配对范围
    c = eng.cooc
    c.begin_session("s1")
    c.log_recall("角度一", [1, 2], {})
    c.log_recall("角度二", [3, 4], {})
    keys = set(c.edges)
    for x in (1, 2):
        for y in (3, 4):
            assert ((x, "atom"), (y, "atom")) in keys
    assert ((1, "atom"), (2, "atom")) not in keys
    assert ((3, "atom"), (4, "atom")) not in keys
    assert len(c.edges) == 4


def test_no_session_no_pairs():
    eng, _ = make_tree_eng()
    c = eng.cooc
    c.log_recall("q1", [1, 2], {})
    c.log_recall("q2", [3, 4], {})
    assert len(c.edges) == 0, "未开会话 → 查询独立，零 recall 边"


def test_cross_query_theta_filter():
    eng, _ = make_eng({"theta_nr": 0.58})
    eng.write(["完全相同的一句话。"])
    eng.write(["完全相同的一句话。"])
    c = eng.cooc
    c.begin_session("s")
    c.log_recall("qa", [1], {})
    c.log_recall("qb", [2], {})
    assert len(c.edges) == 0, "相似度 ≥ θ 的跨查询对不记"


# ── 使用事件汇入（叉口 2/3）──────────────────────────────

def test_usage_event_feeds_edges_evidence_demand_and_concept():
    eng, llm = make_tree_eng({"N": 6, "M": 2, "theta_coo": 5.0})
    c = eng.cooc
    eps = [(i, "atom", "T1") for i in range(1, 7)]
    for _ in range(2):
        c.note_usage({"round": eng.round, "s_ref": None,
                      "s_text": "六张卡一起被使用。", "endpoints": eps})
    assert len(c.edges) == 15                       # 6 成员两两
    assert all(round(v, 3) == 6.0 for v in c.edges.values())   # 2 事件 × T1=3
    comps = [x for x in c._components(5.0) if len(x) == 6]
    assert comps and c._demand(comps[0]) >= 2
    assert all(c._evidence[(i, "atom")] == 6.0 for i in range(1, 7))
    assert c.maybe_create() == 1
    concept = list(c.concepts.values())[0]
    assert concept["trigger_outputs"] == ["六张卡一起被使用。"]
    assert "六张卡一起被使用。" in llm.prompts[-1]


def test_t3_edges_but_no_evidence():
    eng, _ = make_tree_eng()
    c = eng.cooc
    c.note_usage({"round": eng.round, "s_ref": None, "s_text": "T3 使用",
                  "endpoints": [(1, "atom", "T3"), (2, "atom", "T3")]})
    assert c._evidence == {}, "T3 不进成员证据分"
    assert len(c.edges) == 1, "T3 仍进边（最低权）"


def test_member_order_evidence_over_connweight():
    eng, _ = make_tree_eng()
    c = eng.cooc
    comp = [(i, "atom") for i in range(1, 7)]
    for a, b in combinations(comp, 2):
        c._add_pair(a, b, 5.0, "test")              # conn_weight 全等
    c._evidence[(1, "atom")] = 10.0
    c._create(comp)
    members = c.concepts[1]["members"]
    assert (members[0]["id"], members[0]["type"]) == (1, "atom")
    assert members[0]["evidence"] == 10.0


def test_concept_prompt_t1_t2_in_t3_out():
    eng, llm = make_tree_eng({"N": 6, "M": 2, "theta_coo": 5.0})
    c = eng.cooc
    eps = [(i, "atom", "T1") for i in range(1, 7)]
    c.note_usage({"round": eng.round, "s_ref": None, "s_text": "T1 输出句子。",
                  "endpoints": eps})
    c.note_usage({"round": eng.round, "s_ref": None, "s_text": "T1 输出句子。",
                  "endpoints": eps})
    c.note_usage({"round": eng.round, "s_ref": None, "s_text": "T3 输出句子。",
                  "endpoints": [(i, "atom", "T3") for i in range(1, 7)]})
    assert c.maybe_create() == 1
    prompt = llm.prompts[-1]
    assert "T1 输出句子。" in prompt
    assert "T3 输出句子。" not in prompt


def test_trigger_outputs_fallback_no_outputs():
    eng, llm = make_tree_eng({"N": 6, "M": 2, "theta_coo": 5.0})
    c = eng.cooc
    comp = [(i, "atom") for i in range(1, 7)]
    for a, b in combinations(comp, 2):
        c._add_pair(a, b, 5.0, "test")
    c._create(comp)
    prompt = llm.prompts[-1]
    assert "【触发输出】\n（无）" in prompt       # 无输出 → 空段（不编造）
    assert "【触发问题】" in prompt


# ── bring 交集/补齐（叉口 3）──────────────────────────────

def test_bring_intersection_then_fallback():
    eng, _ = make_tree_eng()
    c = eng.cooc
    members = [{"id": i, "type": "atom", "conn_weight": 1.0,
                "evidence": float(10 - i)} for i in range(1, 7)]
    c.concepts[1] = {"id": 1, "title": "t", "content": "c",
                     "embedding": eng._concept_embed("主题"),
                     "members": members, "status": "active",
                     "trigger_queries": [], "trigger_outputs": [],
                     "created_round": 0}
    c._add_pair((7, "atom"), (3, "atom"), 9.0, "test")   # 锚 7 ↔ 成员 3
    buf = io.StringIO()
    c._f_concept = buf
    out = c.bring("查询", direct=[7])
    assert 3 in out
    brings = [json.loads(l) for l in buf.getvalue().splitlines()
              if l.strip() and json.loads(l).get("op") == "bring"]
    assert brings and brings[0]["item"] == 3
    assert brings[0]["via"] == "intersection"
    assert brings[0]["edge_weight"] == 9.0
    assert all(r["via"] == "fallback" for r in brings[1:])


# ── 事件契约 / 装配 ──────────────────────────────────────

def test_usage_layer_event_contract():
    eng, _ = make_tree_eng()
    events = []
    layer = UsageLayer(eng, on_event=events.append)
    layer.record_output([('(依据:#1 "引文")', None)], injected_ids=set())
    assert events
    assert events[0]["s_text"].startswith("(依据:#1")
    assert events[0]["endpoints"] == [(1, "atom", "T1")]


def test_concept_engine_selforg_wires_usage_to_cooc():
    eng, _ = make_tree_eng(selforg=True)
    assert eng.usage is not None
    eng.usage.record_output([('(依据:#1 "引文") (依据:#2 "引文")', None)],
                            injected_ids=set())
    # 使用事件应已进入 cooc 的需求/边：T1 对权重 3
    key = ((1, "atom"), (2, "atom"))
    assert key in eng.cooc.edges and eng.cooc.edges[key] == 3.0
