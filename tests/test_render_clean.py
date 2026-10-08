"""测试：渲染分层（2026-09-25 拍定）。

- clean：原子层=对话形态（连续同来源折叠为一段、段首〔来源〕、无 #id/·原文）；
  影子保留〔召回·来源〕标注；卡片层（摘要/影子节点）保留 §4.5 从属标注。
- indexed（默认）：保留 #id——bench/chatbench 的引用协议依赖，不受影响。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "realtest"))
from realengine import RealEngine


class FakeLLM:
    def chat(self, system, user):
        return "【事实】中性摘要。", {"prompt_tokens": 1, "completion_tokens": 1}, 0.01


def mk(clean=True, **kw):
    return RealEngine(llm=FakeLLM(), style_alpha=0.0, render_clean=clean, **kw)


def test_clean_segments_by_source_and_round():
    eng = mk()
    eng.insert_units([("今天天气不错。", {"source": "user:probe"}),
                      ("你那边呢？", {"source": "user:probe"}),
                      ("我这边没有天气。", {"source": "ai"})], left_id=eng.tail)
    out = eng.render_emission(eng.assemble(budget=10000))
    assert out.count("▸〔user:probe〕") == 1     # 连续同来源折叠为一段
    assert "▸〔ai〕" in out
    assert "#" not in out and "·原文" not in out and "已回复" not in out
    assert "今天天气不错。" in out and "我这边没有天气。" in out


def test_clean_source_labels_map_to_names():
    """展示层来源标签映射（2026-10-03）：ai→名字/self，persona/system→system。"""
    eng = mk()
    eng.insert_units([("我这边没有天气。", {"source": "ai"}),
                      ("你那边呢？", {"source": "user:probe"}),
                      ("【人格】你理性冷静。", {"source": "persona"})], left_id=eng.tail)
    eng.source_labels = {"ai": "林深", "persona": "system", "system": "system"}
    out = eng.render_emission(eng.assemble(budget=10000))
    assert "▸〔林深〕" in out
    assert "▸〔ai〕" not in out and "▸〔persona〕" not in out
    assert "▸〔system〕" in out              # 人格块/系统消息统一 system
    assert "▸〔user:probe〕" in out          # 未映射的来源保持原样


def test_clean_shadow_marked():
    eng = mk()
    ids = eng.insert_units([("旧句一。", {"source": "user:probe"})], left_id=eng.tail)
    # 原子已被节点覆盖（不在铺陈基础层）→ 召回时作为影子注入
    run = eng._new_node()
    run.children = [ids[0]]
    run.value = "【事实】旧句概括。"
    eng.atoms[ids[0]].owner = run.id
    eng.tiling = [run.id]
    out = eng.render_emission(eng.assemble(shadows=ids, budget=10000))
    assert "〔召回·user:probe〕" in out
    assert "旧句一。" in out


def test_clean_children_keep_affiliation_mark():
    """脏节点递归展开的子原子在 clean 下仍带 ⊂父卡（§4.5 硬要求）。"""
    eng = mk()
    ids = eng.insert_units([("甲句。", {"source": "user:probe"}),
                            ("乙句。", {"source": "ai"}),
                            ("丙句。", {"source": "user:probe"})], left_id=eng.tail)
    run = eng._new_node()
    run.children = [ids[0], ids[1]]
    for c in (ids[0], ids[1]):
        eng.atoms[c].owner = run.id
    run.dirty = True
    run.value = None
    eng.tiling = [run.id, ids[2]]
    out = eng.render_emission(eng.assemble(budget=10000))
    assert f"⊂N{run.id}" in out
    assert "#" not in out


def test_indexed_mode_keeps_ids():
    eng = mk(clean=False)
    eng.insert_units([("今天天气不错。", {"source": "user:probe"})], left_id=eng.tail)
    out = eng.render_emission(eng.assemble(budget=10000))
    assert "·原文" in out and "〔user:probe〕" in out


def test_node_sources_recomputed_per_assembly():
    """来源聚合缓存必须每次组装重算（2026-09-25 修复：原清理只在死代码里，
    生产路径从未清理 → 来源漂移防护失效）。"""
    eng = mk(clean=False)
    ids = eng.insert_units([("甲句。", {"source": "user:probe"})], left_id=eng.tail)
    run = eng._new_node()
    run.children = [ids[0]]
    run.value = "【事实】概括。"
    eng.atoms[ids[0]].owner = run.id
    eng.tiling = [run.id]
    out1 = eng.render_emission(eng.assemble(budget=10000))
    assert "〔源:user:probe〕" in out1
    eng.atoms[ids[0]].metadata["source"] = "ai"
    out2 = eng.render_emission(eng.assemble(budget=10000))
    assert "〔源:ai〕" in out2 and "〔源:user:probe〕" not in out2
