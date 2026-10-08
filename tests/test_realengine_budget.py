"""测试：RealEngine 字符预算组装（F2 修复）+ α 门控【注】（F1）+ worklog 归一（P1-4）。

core 桩引擎的 assemble 保持条目数替身语义（见 test_assembly.py）；本文件测生产路径。
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "realtest"))
from realengine import RealEngine


class FakeLLM:
    def __init__(self, text="【事实】中性摘要。"):
        self.text = text
        self.calls = 0
        self.last_prompt = ""

    def chat(self, system, user):
        self.calls += 1
        self.last_prompt = str(user)
        return self.text, {"prompt_tokens": 1, "completion_tokens": 1}, 0.01


def mk(**kw):
    kw.setdefault("decay", 5.0)
    kw.setdefault("attention_in_decay", 1.0)   # 中性衰减：隔离注意力窗口效应
    kw.setdefault("attention_out_decay", 1.0)
    kw.setdefault("distill_parallel", 1)
    return RealEngine(FakeLLM(), encoder=None, **kw)


# ── F2：字符预算 ──

def test_assembly_char_budget_trims_low_hp_nodes():
    eng = mk(assembly_budget=100000)  # 先放宽合并
    eng.write([f"第{i}句内容。" for i in range(20)])
    eng.tick(15)
    eng.merge_pass()          # 20 原子 → 1 节点
    eng.write(["新原子一。", "新原子二。"])
    node = [nid for nid, nd in eng.nodes.items() if nd.alive][0]
    emitted = eng._emit()
    assert emitted[0][0] == "node" and emitted[0][1] == node
    full_chars = eng._emission_chars(emitted)
    assert full_chars > 0
    # 预算 = 一半字符 → 裁剪后应小于预算
    out = eng.assemble(budget=full_chars // 2)
    assert eng._emission_chars(out) <= full_chars // 2


def test_assembly_char_budget_never_drops_atoms():
    eng = mk(assembly_budget=100000)
    eng.write(["一。", "二。", "三。", "四。", "五。"])
    out = eng.assemble(budget=1)   # 预算 1 字符 → 本轮新原子受保护 → 硬截断
    kinds = [k for k, _, _ in out]
    assert all(k == "atom" for k in kinds)
    assert any(e[0] == "hard_trim" for e in eng.events)
    assert len(out) >= 1


def test_trim_cuts_old_atoms_low_hp_first_and_protects_new_round():
    """2026-09-27 拍定：超预算裁旧原子（低 HP 优先）；本轮新建原子至少活一轮。"""
    eng = mk(assembly_budget=100000)
    eng.write([f"旧句子{i}号。" for i in range(6)])
    eng.tick(2)                    # 跨轮 → 6 个旧原子可裁
    eng.write(["新原子一。", "新原子二。"])
    old_ids = [i for i, a in eng.atoms.items() if a.created_round < eng.round]
    new_ids = [i for i, a in eng.atoms.items() if a.created_round == eng.round]
    assert len(old_ids) == 6 and len(new_ids) == 2
    eng.atoms[old_ids[0]].hp = 5.0     # 手工制造 HP 梯度
    eng.atoms[old_ids[1]].hp = 10.0
    emitted = eng._emit()
    total = eng._emission_chars(emitted)
    assert all(k == "atom" for k, _, _ in emitted), "无节点，纯原子铺陈"
    out = eng.assemble(budget=total // 2, allow_merge=False)
    ids = [i for k, i, _ in out]
    assert eng._emission_chars(out) <= total // 2
    assert all(i in ids for i in new_ids), "本轮新原子保护一轮"
    assert old_ids[0] not in ids and old_ids[1] not in ids, "最低 HP 旧原子先裁"
    assert any(e[0] == "trim_atoms" for e in eng.events)
    # 树状态不变：裁剪只影响视野
    assert all(eng.atoms[i].alive for i in old_ids), "裁剪只影响视野，树状态不变"


def test_assembly_char_budget_over_flow_triggers_merge():
    eng = mk(assembly_budget=100000)
    eng.write([f"句子{i}号。" for i in range(30)])
    eng.tick(15)
    before = eng.distill["calls"]
    eng.assemble(budget=100)   # 30 原子渲染远超 100 字符 → 触发 merge_pass
    assert eng.distill["calls"] > before


def test_hard_trim_slack_tolerates_minor_overage():
    """硬截断松弛：溢出 < slack 不砍（超 40%，slack 50%）。"""
    eng = mk(assembly_budget=100000, hard_trim_slack=0.5)
    eng.write([f"句子{i}号。" for i in range(10)])
    total = eng._emission_chars(eng._emit())
    budget = int(total / 1.4)   # 超 40% < slack 50% → 不硬截断
    out = eng.assemble(budget=budget, allow_merge=False)
    assert len(out) == 10
    assert not any(e[0] == "hard_trim" for e in eng.events)


def test_hard_trim_slack_still_cuts_big_overflow():
    eng = mk(assembly_budget=100000, hard_trim_slack=0.5)
    eng.write([f"句子{i}号。" for i in range(10)])
    total = eng._emission_chars(eng._emit())
    budget = int(total / 1.8)   # 超 80% > slack 50% → 硬截断
    out = eng.assemble(budget=budget, allow_merge=False)
    assert any(e[0] == "hard_trim" for e in eng.events)
    assert len(out) < 10


# ── F1：α 门控【注】──

def test_alpha_zero_forces_note_empty():
    llm = FakeLLM(text="【事实】生日是 3 月 14 日。\n【注】这日子有点意思")
    eng = RealEngine(llm, encoder=None, distill_parallel=1, style_alpha=0.0)
    out = eng._summarize(["probe 说生日是 3 月 14 日。"])
    assert "【注】" not in out
    assert "生日" in out


def test_alpha_one_note_optional():
    llm = FakeLLM(text="【事实】生日是 3 月 14 日。")
    eng = RealEngine(llm, encoder=None, distill_parallel=1, style_alpha=1.0)
    out = eng._summarize(["probe 说生日是 3 月 14 日。"])
    assert "【注】" not in out          # LLM 不加【注】也不违规（可加可不加）
    assert "生日" in out


def test_alpha_one_note_kept_when_present():
    llm = FakeLLM(text="【事实】生日是 3 月 14 日。\n【注】π 日同款")
    eng = RealEngine(llm, encoder=None, distill_parallel=1, style_alpha=1.0)
    out = eng._summarize(["probe 说生日是 3 月 14 日。"])
    assert "【注】π 日同款" in out


# ── P1-4：worklog 固定 key 覆盖 ──

def test_worklog_key_normalized_to_current():
    eng = mk()
    eng.nb_set("第1轮", "worklog", "第一条记录")
    eng.nb_set("第2轮", "worklog", "第二条记录")
    worklogs = [e for e in eng.notebook.values() if e["kind"] == "worklog"]
    assert len(worklogs) == 1
    assert worklogs[0]["content"] == "第二条记录"
    assert worklogs[0]["key"] == "current"


# ── join 无超时修复（2026-08-28 主实验 neutral 卡死 8 分钟根因）──

class HangingLLM:
    """模拟 DeepSeek 挂起：不返回也不抛异常。"""
    def __init__(self, hang=5.0, text="【事实】中性摘要。"):
        self.hang = hang
        self.text = text
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        import time
        time.sleep(self.hang)
        return self.text, {"prompt_tokens": 1, "completion_tokens": 1}, 0.01


def test_merge_runs_hanging_worker_timeout_no_deadlock():
    """蒸馏 worker 挂起 → batch 超时标 failed → merge 现场兜底完成，不死锁。
    （无超时 join 会无限等待=2026-08-28 neutral 卡死根因；修复后：
     批等待 ≤0.3s，现场兜底每 chunk ≤ hang 时长，整体有界）"""
    eng = RealEngine(HangingLLM(hang=5.0), encoder=None, distill_parallel=2,
                     decay=5.0, attention_in_decay=1.0, attention_out_decay=1.0,
                     distill_batch_timeout=0.3)
    eng.write([f"第{i}句内容。" for i in range(30)])
    eng.tick(15)
    t0 = time.time()
    eng.merge_pass()
    wall = time.time() - t0
    assert wall < 30.0, f"merge_pass 卡死: wall={wall:.1f}s"
    # 超时未完成的缓存被 distill_cache_take 消费（pop+计数 wasted）→ 现场兜底蒸馏
    assert eng.distill_cache_misses > 0, "应存在被超时标记并消费的 failed 缓存"


def test_daemon_pool_caps_concurrency_submit_never_blocks():
    """池并发被 max_workers 限死；submit 永不阻塞（队列缓冲）。"""
    import threading
    eng = mk(distill_parallel=2)
    pool = eng._distill_pool
    started = []
    ev = threading.Event()

    def slow(n):
        started.append(n)
        ev.wait()   # 全部任务挂起：验证同时执行数 ≤ 并发上限
        return n

    f0 = pool.submit(slow, 0)
    f1 = pool.submit(slow, 1)
    f2 = pool.submit(slow, 2)   # 应排队，不阻塞调用
    time.sleep(0.15)
    assert len(started) == 2, f"并发应被限死为 2，实际 {len(started)}"
    ev.set()
    assert f0.result(timeout=2) == 0
    assert f2.result(timeout=2) == 2


# ── B2：使用度沿树上继承（2026-08-29 拍定，属性测试断言）──

def test_merge_parent_hp_inherits_group_usage():
    """高 HP 成员合并 → 父 HP 高；老重组 → 父保底 70。
    注意：merge 条件 = HP < 30，故组均 HP ∈ [0,30)，父 HP ∈ [70,100)。
    继承幅度 = 组均（最大 ~30 = recall_boost 量级）。"""
    eng = mk()
    ids = eng.write([f"句{i}。" for i in range(4)])
    for aid in ids:
        eng.atoms[aid].hp = 25.0   # 低但非零（近期合并的组）
    eng.merge_pass()
    hot_parent = [nd for nd in eng.nodes.values() if nd.alive]
    assert hot_parent, "应有父节点"
    assert hot_parent[0].hp == pytest.approx(95.0)  # 70 + 25
    # 老重组：成员 HP=0 → 父 = 70 保底
    eng2 = mk()
    ids2 = eng2.write([f"句{i}。" for i in range(4)])
    for aid in ids2:
        eng2.atoms[aid].hp = 0.0
    eng2.merge_pass()
    cold_parent = [nd for nd in eng2.nodes.values() if nd.alive]
    assert cold_parent[0].hp == pytest.approx(70.0), "老重组父 HP = 保底 70"


def test_merge_parent_hp_never_exceeds_max():
    """父 HP 恒 ≤ max_hp（合并路径组均 <30，父 ≤100；属性断言）。"""
    eng = mk()
    ids = eng.write([f"句{i}。" for i in range(4)])
    for aid in ids:
        eng.atoms[aid].hp = 29.9
    eng.merge_pass()
    p = [nd for nd in eng.nodes.values() if nd.alive][0]
    assert p.hp <= 150.0


def test_node_window_slow_decay_and_no_cliff():
    """节点窗内慢衰减（×in_decay）；出窗 = decay（无 3x 悬崖）。"""
    eng = mk()
    ids = eng.write([f"句{i}。" for i in range(4)])
    for aid in ids:
        eng.atoms[aid].hp = 0.0
    eng.merge_pass()
    p = [nd for nd in eng.nodes.values() if nd.alive][0]
    p.hp = 70.0
    eng.tick(1)
    # 窗内（HP 70 ≥ 45 且在 node_cap 前 20）：-5（decay5 × 0.5）
    assert p.hp == pytest.approx(65.0)
    p.hp = 30.0
    eng.tick(1)
    # 出窗：-5（decay），不是 -15（悬崖）
    assert p.hp == pytest.approx(25.0)


# ── 早期标记（2026-10-03 新语义）：不再按 HP；改为与"失效参照集"
#    （墓碑原子 + 脏节点旧文本）的 bigram Jaccard ≥ early_match_threshold ──

def test_is_early_matches_tombstone():
    eng = mk()
    ids = eng.write(["我在学 Rust。", "我改学 Go 了。"])
    assert not eng._is_early(eng.atoms[ids[0]].value)   # 无失效参照 → 不早
    eng.atoms[ids[0]].alive = False                     # 模拟编辑墓碑
    assert eng._is_early("我在学 Rust。")                # 与失效内容相似 → 早期
    assert not eng._is_early("今天天气不错。")


def test_is_early_matches_dirty_node():
    eng = mk()
    import core
    ids = eng.write(["地窖里有红绳。"])
    nid = eng._nid()
    nd = core.Node(id=nid, value="【事实】地窖里有红绳。", dirty=True)
    nd.children = list(ids)
    eng.nodes[nid] = nd
    assert eng._is_early("地窖里有红绳。")


def test_group_parts_clean_segmented():
    """乙：蒸馏输入 = 与主模型同款 `▸〔来源〕` 分段 + 原文，无逐句内联标签。"""
    eng = mk()
    ids = eng.insert_units([("我在学 Rust。", {"source": "user:probe"}),
                            ("我改学 Go 了。", {"source": "ai"})], left_id=eng.tail)
    assert eng._group_parts(ids) == ["▸〔user:probe〕\n  我在学 Rust。",
                                     "▸〔ai〕\n  我改学 Go 了。"]


def test_group_parts_marks_early_on_match():
    eng = mk()
    dead = eng.write(["我在学 Rust。"])
    eng.atoms[dead[0]].alive = False                # 失效参照
    live = eng.write(["我在学 Rust。"])              # 同内容新原子（无来源）
    assert eng._group_parts(live) == ["▸〔早期〕\n  我在学 Rust。"]


def test_weighted_avg_prompt_is_clean():
    """现场蒸馏：prompt 已去脚手架规则（无时序/来源标注），输入为分段渲染。"""
    eng = mk()
    ids = eng.write(["我在学 Rust。", "我改学 Go 了。"])
    eng._distill_cache = {}
    out = eng._weighted_avg(ids)
    assert out is not None
    assert eng.llm.last_prompt
    assert "时序分流规则" not in eng.llm.last_prompt
    assert "来源标注规则" not in eng.llm.last_prompt
    assert "▸" in eng.llm.last_prompt


def test_prompt_has_no_scaffold_rules():
    from realtest.realengine import SUMMARIZE_PROMPT
    assert "时序分流规则" not in SUMMARIZE_PROMPT
    assert "来源标注规则" not in SUMMARIZE_PROMPT
    assert "〔早期〕" not in SUMMARIZE_PROMPT


# ── 内存修复（2026-10-03）：节点/原子向量批量编码，防 MPS 幽灵内存 ──

class CountingEncoder:
    def __init__(self):
        self.calls = 0
        self.batch_sizes = []

    def encode(self, texts, normalize_embeddings=True, batch_size=None):
        n = 1 if isinstance(texts, str) else len(texts)
        self.calls += 1
        self.batch_sizes.append(n)
        return [[0.0, 0.0, 0.0, 0.0] for _ in range(n)]


def test_ensure_node_vecs_batches_once():
    import core
    enc = CountingEncoder()
    eng = RealEngine(FakeLLM(), encoder=enc, distill_parallel=1,
                     attention_in_decay=1.0, attention_out_decay=1.0)
    aids = eng.write([f"句子{i}内容。" for i in range(10)])
    nids = []
    for i in range(0, 10, 2):
        nid = eng._nid()
        nd = core.Node(id=nid, value=f"摘要{i}", children=aids[i:i + 2], alive=True)
        eng.nodes[nid] = nd
        nids.append(nid)
    enc.calls = 0
    eng._ensure_node_vecs(nids)
    assert enc.calls == 1                 # 全部 miss → 一次批量 encode
    assert enc.batch_sizes == [len(nids)]
    eng._ensure_node_vecs(nids)           # 二次：全命中缓存 → 不再 encode
    assert enc.calls == 1


def test_ensure_atom_vecs_batches_once():
    enc = CountingEncoder()
    eng = RealEngine(FakeLLM(), encoder=enc, distill_parallel=1,
                     attention_in_decay=1.0, attention_out_decay=1.0)
    aids = eng.write([f"原子{i}文本内容。" for i in range(8)])
    enc.calls = 0
    eng._ensure_atom_vecs(aids)
    assert enc.calls == 1
    eng._ensure_atom_vecs(aids)
    assert enc.calls == 1
