"""测试：人格锚点检索化（2026-10-03）。

覆盖：
- retrieve_style_samples：只取自身材料（source=ai 原子 + 摘要节点），排除他人/工具；
  节点贡献整卡（含【注】）；exclude_ids 排除子树；跳过 dirty/placeholder。
- expand_self_atoms：节点 → 仅自身原话原子，cap 生效。
- render_emission：【注】不渲染（只渲染【事实】）。
- _summarize：children 透传给 style_provider（2参），旧式 0 参 provider 兼容。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "realtest"))
from realengine import RealEngine  # noqa: E402
import core  # noqa: E402


class FakeLLM:
    def chat(self, system, user):
        return "【事实】摘要。\n【注】我的口吻。", {}, 0.0


def make_engine(**kw):
    e = RealEngine(FakeLLM(), distill_parallel=1, **kw)
    e._encoder_model = lambda: None   # 强制关键词通道，避免测试加载 bge
    return e


def _add_node(eng, atom_texts, value):
    ids = eng.insert_units([(t, {"source": "ai"}) for t in atom_texts],
                           left_id=eng.tail)
    nid = eng._nid()
    nd = core.Node(id=nid, value=value)
    nd.children = list(ids)
    for aid in ids:
        eng.atoms[aid].owner = nid
    eng.nodes[nid] = nd
    # 手工建节点后同步 tiling（用节点替换其原子），保证后续 insert 的 tiling 不变量
    idset = set(ids)
    new_tiling = []
    for x in eng.tiling:
        if x in idset:
            if nid not in new_tiling:
                new_tiling.append(nid)
        else:
            new_tiling.append(x)
    eng.tiling = new_tiling
    return nid, ids


# ── retrieve_style_samples ──

def test_retrieve_own_only():
    e = make_engine()
    own = e.insert_units([("我偏爱用账本和格子的比喻。", {"source": "ai"})], left_id=e.tail)
    inc = e.insert_units([("用户提到账本格子。", {"source": "user:x"})], left_id=e.tail)
    tool = e.insert_units([("工具输出 账本格子。", {"source": "tool"})], left_id=e.tail)
    hits = e.retrieve_style_samples("账本 格子", top_k=5)
    ids = [h["id"] for h in hits]
    assert own[0] in ids
    assert inc[0] not in ids
    assert tool[0] not in ids


def test_retrieve_node_whole_card_has_note():
    e = make_engine()
    nid, _ = _add_node(e, ["账本记录了很多格子。"],
                       "【事实】账本记录了格子。\n【注】用我自己的口吻。")
    hits = e.retrieve_style_samples("账本 格子", top_k=5)
    node_hits = [h for h in hits if h["type"] == "node"]
    assert node_hits, hits
    assert "【注】" in node_hits[0]["text"]
    assert "【事实】" in node_hits[0]["text"]


def test_retrieve_strips_scaffold_prefixes():
    e = make_engine()
    _add_node(e, ["账本格子。"],
              "【事实】〔ai〕〔早期〕账本记录了格子。〔另有1条未展开，可@span下钻〕\n【注】我的口吻。")
    hits = e.retrieve_style_samples("账本 格子", top_k=5)
    text = " ".join(h["text"] for h in hits)
    assert "〔ai〕" not in text
    assert "〔早期〕" not in text
    assert "〔另有" not in text
    assert "账本记录了格子" in text


def test_retrieve_excludes_subtree():
    e = make_engine()
    nid, aids = _add_node(e, ["账本格子甲。", "账本格子乙。"], "【事实】账本格子。")
    # 无排除 → 命中节点/原子；排除该节点 → 节点与其原子都不出现
    hits1 = e.retrieve_style_samples("账本格子", top_k=5)
    assert nid in [h["id"] for h in hits1]
    hits2 = e.retrieve_style_samples("账本格子", top_k=5, exclude_ids=[nid])
    got = [h["id"] for h in hits2]
    assert nid not in got
    assert not (set(aids) & set(got))


def test_retrieve_skips_dirty_placeholder():
    e = make_engine()
    n_dirty, _ = _add_node(e, ["账本格子脏。"], "【事实】账本格子脏。")
    e.nodes[n_dirty].dirty = True
    n_ph, _ = _add_node(e, ["账本格子占位。"], "【事实】账本格子占位。")
    e.nodes[n_ph].placeholder = True
    hits = e.retrieve_style_samples("账本格子", top_k=5)
    got = [h["id"] for h in hits]
    assert n_dirty not in got and n_ph not in got


def test_retrieve_empty_query():
    e = make_engine()
    e.insert_units([("账本格子。", {"source": "ai"})], left_id=e.tail)
    assert e.retrieve_style_samples("", top_k=3) == []


# ── expand_self_atoms ──

def test_expand_self_atoms_only_ai_and_cap():
    e = make_engine()
    nid, aids = _add_node(e, ["我的原话一。", "我的原话二。", "我的原话三。"],
                          "【事实】概括。")
    # 混入一条他人原子
    e.insert_units([("别人的话。", {"source": "user:x"})], left_id=e.tail)
    out = e.expand_self_atoms(nid, cap=2)
    assert len(out) == 2
    assert all("原话" in t for t in out)


# ── 渲染：【注】不渲染 ──

def test_render_strips_note_keeps_fact():
    e = make_engine()
    nid, _ = _add_node(e, ["账本格子。"],
                       "【事实】账本记录格子。\n【注】我的口吻。")
    rendered = e.render_emission([("node", nid, None)])
    assert "【注】" not in rendered
    assert "账本记录格子" in rendered


# ── _summarize：children 透传 / 旧式无参兼容 ──

def test_summarize_passes_children_to_provider():
    seen = {}

    class E2(RealEngine):
        pass

    e = E2(FakeLLM(), distill_parallel=1, style_alpha=1.0)
    e._encoder_model = lambda: None

    def provider(content, children=None):
        seen["content"] = content
        seen["children"] = list(children or [])
        return "锚点"

    e.style_provider = provider
    parts = ["源内容一。", "源内容二。"]
    out = e._summarize(parts, children=[11, 22])
    assert "锚点" not in out          # 锚点只进 prompt，不进摘要正文
    assert seen["children"] == [11, 22]
    assert "源内容" in seen["content"]


def test_style_provider_noarg_fallback():
    called = []
    e = make_engine(style_alpha=1.0)
    e.style_provider = lambda: called.append(1) or "风格指南"
    e._summarize(["一段", "两段"])
    assert called == [1]
