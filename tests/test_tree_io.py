"""测试：树 gzip 持久化（tree_io）——含轨道B 笔记本/懒标记 roundtrip。

背景（2026-09-25）：ahi-bench M0+quiz variant 需要把 ahi 里演化好的树落盘，
供后续环节加载做 QA/实验分析；notebook（facts + dismiss 懒标记）必须随树保存。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "realtest"))
from realengine import RealEngine

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
from realtest.chatbench.tree_io import (save_tree, load_tree,
                                        latest_tree_snapshot)


class FakeLLM:
    def chat(self, system, user):
        return "【事实】中性摘要。", {"prompt_tokens": 1, "completion_tokens": 1}, 0.01


def mk():
    return RealEngine(llm=FakeLLM(), style_alpha=0.0)


def test_tree_io_notebook_roundtrip():
    eng = mk()
    eng.insert_units([("你好世界。", {"source": "user:u"}),
                      ("第二句内容。", {"source": "ai"}),
                      ("第三条。", {"source": "tool"})],
                     left_id=eng.tail)
    eng.nb_set("key1", "state", "内容A")
    eng.nb_set("dismiss_1", "schedule", "冲突描述", status="planted")
    n_atoms = len([a for a in eng.atoms.values() if a.alive])

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json.gz")
        save_tree(eng, path)

        eng2 = mk()
        load_tree(eng2, path)
        assert len([a for a in eng2.atoms.values() if a.alive]) == n_atoms
        assert eng2.head == eng.head and eng2.tail == eng.tail
        assert eng2.tiling == eng.tiling
        assert eng2.notebook["key1"]["content"] == "内容A"
        assert eng2.notebook["dismiss_1"]["status"] == "planted"
        # 加载后可继续演化（结构自洽）
        ids = eng2.insert_units([("第四条。", {"source": "user:u"})],
                                left_id=eng2.tail)
        assert ids


def test_tree_io_stub_engine_compat():
    """桩引擎无 notebook 属性 → save/load 兜底不崩（旧调用方兼容）。"""
    from core import Engine
    e = Engine()
    e.write([1.0, 2.0])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "stub.json.gz")
        save_tree(e, path)
        e2 = Engine()
        load_tree(e2, path)
        assert len([a for a in e2.atoms.values() if a.alive]) == 2


def test_agent_state_roundtrip():
    """agent 级状态（重启恢复）随树同文件保存/加载。"""
    eng = mk()
    eng.insert_units([("一句。", {"source": "ai"})], left_id=eng.tail)
    state = {"round": 37, "persona_on": False, "snapshot": "风格快照",
             "last_force_round": 30, "promote_count_last": 4,
             "anchor_query_buf": ["旧蒸馏内容"]}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json.gz")
        save_tree(eng, path, agent_state=state)
        eng2 = mk()
        load_tree(eng2, path)
        assert eng2._restored_agent_state == state


def test_agent_state_absent_is_none():
    """旧快照（无 agent_state）加载后为 None，不崩。"""
    eng = mk()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json.gz")
        save_tree(eng, path)
        eng2 = mk()
        load_tree(eng2, path)
        assert eng2._restored_agent_state is None


def test_latest_tree_snapshot_selection():
    """优先周期性 rN（最大 N）；无周期性回退 final（mtime 最新）。"""
    with tempfile.TemporaryDirectory() as d:
        aid = "tree-p1"
        assert latest_tree_snapshot(d, aid) is None
        f10 = os.path.join(d, f"{aid}_tree_r10.json.gz")
        f20 = os.path.join(d, f"{aid}_tree_r20.json.gz")
        ff = os.path.join(d, f"{aid}_tree_final.json.gz")
        for p in (f10, f20, ff):
            with open(p, "w") as f:
                f.write("x")
        assert latest_tree_snapshot(d, aid) == f20
        os.remove(f20)
        assert latest_tree_snapshot(d, aid) == f10
        os.remove(f10)
        assert latest_tree_snapshot(d, aid) == ff
        # 其它 agent 的快照不得混入
        assert latest_tree_snapshot(d, "tree-p2") is None
