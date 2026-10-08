"""test_editstorm.py — 编辑风暴实验（editbench）确定性部件测试（零 LLM）。"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "realtest"))
sys.path.insert(0, os.path.join(_ROOT, "realtest", "editbench"))

from editstorm import (build_scenario, build_scenario_ahi, render_ledger, diff_units,   # noqa: E402
                       rename, detail, delete_sentence, append_paragraph)
from docscore import check_spec, score_retell                       # noqa: E402

_SEED = os.path.join(_ROOT, "realtest", "seed2", "story.txt")


def _doc():
    return open(_SEED, encoding="utf-8").read().strip()


def test_scenario_shape():
    new, ledger = build_scenario(_doc())
    kinds = [e["kind"] for e in ledger]
    assert kinds == ["rename", "rewrite", "rewrite", "detail", "detail", "delete", "append"]
    # 新文档含新内容、不含旧内容（失效完整性）
    for frag in ("何某笙", "三年前就塌了", "一家三口", "三枚银圆", "梨木拐杖", "顾在川"):
        assert frag in new, frag
    for frag in ("何倚笙", "七年前就烧光了", "治过风寒", "两枚银圆", "枣木拐杖"):
        assert frag not in new, frag


def test_scenario_deterministic():
    a, _ = build_scenario(_doc())
    b, _ = build_scenario(_doc())
    assert a == b


def test_ledger_render_and_diff():
    _, ledger = build_scenario(_doc())
    text = render_ledger(ledger)
    assert "编辑记录" in text and "何某笙" in text and "顾在川" in text
    du = diff_units(ledger)
    assert du and all(isinstance(u, str) and u for u in du)


def test_primitives_raise_on_missing_anchor():
    for fn, args in ((rename, ("没有这个人", "某人")),
                     (detail, ("不存在的细节", "x")),
                     (delete_sentence, ("不存在的句子。",))):
        try:
            fn("这是一段测试文本。", *args)
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__} 未对失效锚点报错")


def test_append_never_raises():
    new, rec = append_paragraph("原文。", "新增一段。")
    assert new.startswith("原文") and rec["kind"] == "append"


def _ahi_doc():
    p = os.path.join(_ROOT, "realtest", "editbench", "ahi_script.txt")
    if not os.path.exists(p):
        import pytest
        pytest.skip("ahi_script.txt 未生成（运行 gen_script.py 先）")
    return open(p, encoding="utf-8").read().strip()


def test_scenario_ahi_shape():
    new, ledger = build_scenario_ahi(_ahi_doc())
    kinds = [e["kind"] for e in ledger]
    assert kinds == ["passage", "rename", "detail", "detail", "detail", "detail",
                     "detail", "delete", "delete", "delete", "append"]
    for frag in ("墨某", "读也算越界", "210e489aead0", "双进程", "840 字节", "3000 字",
                 "7 个路径", "root:*:99999", "/etc/motd", "凡是能跑一遍的，不猜"):
        assert frag in new, frag
    for frag in ("读不算越界", "210e489aead9", "单进程", "839 字节", "2000 字",
                 "root:*:20689", "shadow readable", "四面墙，四个高度", "6 个路径",
                 "咱们仨独立验证过了"):
        assert frag not in new, frag
    assert new.count("墨") == new.count("墨某")   # 旧名 墨 无残留


def test_scenario_ahi_deterministic():
    a, _ = build_scenario_ahi(_ahi_doc())
    b, _ = build_scenario_ahi(_ahi_doc())
    assert a == b


def test_full_arm_materializes_function_call_blocks():
    """full 臂：每次编辑渲染成 function-calling 式"调用+返回"块；append-only 保留旧文。"""
    from arms import FullArm

    class Fake:
        model = "fake"

        def chat(self, s, u):
            return "", {"prompt_tokens": 0, "completion_tokens": 0}, 0.0

    arm = FullArm(Fake())
    arm.build("原文A。原文B。")
    arm.apply_ledger([{"kind": "detail", "old": "A", "new": "C",
                       "old_units": ["原文A。"], "new_units": ["原文C。"]}])
    ctx = arm.context("问题")
    assert "原文C。" in ctx                              # 编辑结果 materialize
    assert '<tool_call' in ctx and 'name="edit_document"' in ctx
    assert '"op": "detail"' in ctx and '"old": "A"' in ctx and '"new": "C"' in ctx
    assert "<tool_result>" in ctx
    assert "原文A。" in ctx                              # append-only：旧文保留（腐烂行为）


def test_flat_arm_appends_function_call_blocks():
    """flat 臂 append-only：编辑结果以 function-calling 块进入可检索 chunk。"""
    import numpy as np
    from arms import FlatArm

    class Fake:
        model = "fake"

        def chat(self, s, u):
            return "", {"prompt_tokens": 0, "completion_tokens": 0}, 0.0

    class FakeEnc:
        def encode(self, texts, normalize_embeddings=False, batch_size=32):
            return np.zeros((len(texts), 4), dtype=float)

    arm = FlatArm(Fake(), FakeEnc())
    arm.build("原文A。原文B。")
    arm.apply_ledger([{"kind": "detail", "old": "A", "new": "C",
                       "old_units": ["原文A。"], "new_units": ["原文C。"]}])
    joined = "\n".join(arm.chunks)
    assert "<tool_call" in joined and '"op": "detail"' in joined and "原文C。" in joined


def test_check_spec_require_forbid():
    ok, d = check_spec("答案是三枚银圆", {"require": ["三枚银圆"]})
    assert ok
    ok, d = check_spec("答案是何倚笙", {"forbid": ["何某笙"]})
    assert ok
    ok, d = check_spec("答案是何倚笙", {"require": ["何某笙"]})
    assert not ok and d["require_miss"] == ["何某笙"]


def test_check_spec_sentence_scoped():
    """跨句拼接不误命中（norm_cjk 去标点会把相邻句粘起来）。"""
    ok, d = check_spec("句子一：四面墙。句子二：四个高度。",
                       {"forbid": ["四面墙，四个高度"]})
    assert ok, d                    # 分属两句 → 不算旧内容残留
    ok, d = check_spec("这就是我的身体，四面墙，四个高度。",
                       {"forbid": ["四面墙，四个高度"]})
    assert not ok and d["forbid_hit"] == ["四面墙，四个高度"]   # 同句内 → 命中


def test_run_qa_judge_reviews_failures():
    """判分流水线：确定性初筛失败 → judge 复核可改判为 pass。"""
    from docbench import run_qa

    class _L:
        def chat(self, s, u):
            return "旧的答案是何倚笙。", {"prompt_tokens": 1, "completion_tokens": 1}, 0.0

    class FakeArm:
        def __init__(self):
            self.llm = _L()

        def reset(self):
            pass

        def context(self, q):
            return "ctx"

    class FakeJudge:
        def chat(self, s, u):
            return '{"verdict":"是","reason":"已说明改名"}', {}, 0.0

    q = [{"id": "Q1", "q": "角色叫什么", "layer": "reversal",
          "check_after": {"require": ["何某笙"], "forbid": ["何倚笙"]}}]
    rows = run_qa(FakeArm(), q, "after", FakeJudge())
    assert rows[0]["pass"] and rows[0]["pass_via"] == "judge"
    # 无 judge 时确定性判分保持失败
    rows2 = run_qa(FakeArm(), q, "after", None)
    assert not rows2[0]["pass"]


def test_score_retell_layers():
    sc = score_retell("何某笙顾在川一家三口三枚银圆，没有何倚笙。",
                      {"require": ["何某笙", "顾在川", "一家三口", "三枚银圆"],
                       "forbid": ["何倚笙", "两枚银圆"]})
    assert sc["coverage_ratio"] == 1.0
    assert sc["rot_ratio"] == 0.5
    assert sc["old_leak"] == ["何倚笙"]
