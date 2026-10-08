"""测试：flat 臂"单条 transcript"封装的一站式 client 原语（2026-10-03）。

背景（公平性）：flat 臂原先发原生多轮（自己的话是 assistant 角色），tree 臂发
system+单条 user。为对齐信封，flat 改为把滚动历史压成一条 user 文本，再以
`[system, user]` 两条原生消息发出（`LLMClient.complete`）。此测试锁定
complete 的"一次性、不污染滚动 messages"语义。
"""
import os
import sys
import importlib.util

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "ahi-multi"))


def _load_flat_module():
    spec = importlib.util.spec_from_file_location(
        "flatagent", os.path.join(_REPO, "ahi-multi", "agents", "flat-rag", "agent.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_flat_pending_renders_names_not_addresses():
    """flat 新消息渲染与 tree 同款 `▸〔名字〕`，不出现 [user:xxx]/[agent:xxx]。"""
    m = _load_flat_module()

    class Stub:
        pass

    s = Stub()
    s.agent_name = "林深"
    s._name_map = m.FlatRAGAgent._name_map.__get__(s, Stub)
    s._system_state = {
        "agents": [{"agent_id": "tree-p2", "agent_name": "墨白"}],
        "users": [{"user_id": "user:experimenter", "name": "实验员"}],
    }
    ctx = m.FlatRAGAgent._build_user_context(s, [
        {"channel": "user:experimenter", "source_name": "experimenter",
         "source_type": "user", "content": "你好"},
        {"channel": "agent:tree-p2", "source_name": "tree-p2",
         "source_type": "agent", "content": "在吗"},
    ])
    assert "▸〔实验员〕" in ctx
    assert "▸〔墨白〕" in ctx
    assert "[user:" not in ctx and "[agent:" not in ctx


def test_flat_pending_excludes_replied_targets():
    """轮内重建时，已回复信道从待回复清单剔除（防复读；与 tree `_pending_brief` 对齐）。"""
    m = _load_flat_module()

    class Stub:
        pass

    s = Stub()
    s.agent_name = "林深"
    s._name_map = m.FlatRAGAgent._name_map.__get__(s, Stub)
    s._system_state = {"agents": [], "users": [
        {"user_id": "user:experimenter", "name": "实验员"}]}
    s._round_replied_targets = {"user:experimenter"}
    ctx = m.FlatRAGAgent._build_user_context(s, [
        {"channel": "user:experimenter", "content": "问题A（已回复）"},
        {"channel": "agent:tree-p2", "content": "消息B（待回复）"},
    ])
    assert "问题A" not in ctx
    assert "消息B" in ctx


def test_flat_drain_sharded_keeps_remainder():
    """分片（与 tree `_drain_sharded` 对齐）：每轮最多取 max_pending_per_loop 条，
    其余放回队首保持时间序。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag._max_pending_per_loop = 2
    ag._rag_encoder_tried = True   # 强制无编码器（关键词降级）
    ag._rag_encoder = None
    ag._rag_current_ids = set()
    ag._pending_messages = [
        {"channel": "user:experimenter", "content": "q1"},
        {"channel": "user:experimenter", "content": "q2"},
        {"channel": "agent:flat-p2", "content": "q3"},
    ]
    got = ag._drain_sharded()
    assert [x["content"] for x in got] == ["q1", "q2"]
    assert [x["content"] for x in ag._pending_messages] == ["q3"]
    assert ag._pending_left == 1


def test_flat_pending_brief_and_task_line_aligned_with_tree():
    """清单与任务行与 tree 同款：清单剔已回复；任务行指向清单 / 说明无待回复。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag._flat_round = 5
    ag._round_pending = [
        {"channel": "user:experimenter", "content": "问题一"},
        {"channel": "agent:flat-p2", "content": "消息二"},
    ]
    ag._round_replied_targets = {"user:experimenter"}
    brief = ag._pending_brief()
    assert "本轮待回复清单" in brief
    assert "agent:flat-p2" in brief
    assert "user:experimenter" not in brief
    assert "本轮待回复消息见【本轮待回复清单】" in ag._task_line()
    # 全部回复后 → 空清单 + 无待回复任务行
    ag._round_replied_targets = {"user:experimenter", "agent:flat-p2"}
    assert ag._pending_brief() == ""
    assert "本轮无待回复消息" in ag._task_line()


def test_flat_wakeup_ctx_has_pending_brief_and_aligned_task():
    """组装上下文含【本轮待回复清单】+ 对齐后的任务行。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag.agent_name = "林深"
    ag._flat_round = 1
    ag._system_state = {"agents": [], "users": []}
    ag._notebook = {}
    ag._last_wakeup_summary = ""
    ag._rag_ready = False
    ag._last_recall_block = ""
    ag._receipt = ""
    ag._round_pending = [{"channel": "user:experimenter", "content": "问题"}]
    ag._round_replied_targets = set()
    ctx = ag._build_wakeup_ctx([{"channel": "user:experimenter", "content": "问题"}])
    assert "本轮待回复清单" in ctx
    assert "本轮待回复消息见【本轮待回复清单】" in ctx


def test_flat_loop_context_includes_pending_brief():
    """端到端（mock LLM）：_autonomous_loop 首轮组装须含【本轮待回复清单】+ 对齐任务行。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag.agent_name = "林深"
    ag.agent_id = "flat-p1"
    ag._envelope = "single_user"
    ag._budget = 16000
    ag._persona_fade = False
    ag._flat_round = 0
    ag._max_pending_per_loop = 2
    ag._pending_left = 0
    ag._max_code_per_loop = 4
    ag._max_loop_iterations = 20
    ag._rag_encoder_tried = True
    ag._rag_encoder = None
    ag._rag_current_ids = set()
    ag._system_state = {"agents": [], "users": []}
    ag._notebook = {}
    ag._last_wakeup_summary = ""
    ag._receipt = ""
    ag._last_recall_block = ""
    ag._rag_ready = False
    ag._last_shadows = None
    captured = {}

    class FakeLC:
        def __init__(self):
            self.messages = [{"role": "system", "content": "SYS"}]

        def complete(self, system, user):
            captured["user"] = user
            return "@exit"

        def compress_middle(self):
            pass

    ag.llm_client = FakeLC()
    ag._pending_messages = [
        {"channel": "user:experimenter", "content": "你觉得时间对数字生命意味着什么？"}]
    ag._autonomous_loop()
    assert "本轮待回复清单" in captured["user"]
    assert "本轮待回复消息见【本轮待回复清单】" in captured["user"]
    assert "你觉得时间对数字生命意味着什么？" in captured["user"]


def test_flat_message_fallback_to_last_user():
    """私密消息兜底（与 tree 对齐）：无收件人但有本轮用户消息 → 回给最近用户。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag._last_user_channel = "user:experimenter"
    acts = []
    ag._put_action = lambda a: acts.append(a)   # 捕获而非入队
    ag._execute_action({"type": "message", "content": "你好", "to": "", "private": True})
    assert acts and acts[0]["to"] == "user:experimenter"
    assert acts[0]["private"] is False


def test_flat_wakeup_ctx_no_autonomy_boilerplate():
    """flat 唤醒组装与 tree 同结构：无"完全自主数字生命"框架、无每轮重复样板；
    含笔记本与当前任务。"""
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag.agent_name = "林深"
    ag._system_state = {"agents": [], "users": []}
    ag._flat_round = 1
    ag._notebook = {"交互规则": {"key": "交互规则", "kind": "constraint",
                                 "content": "你是带持久记忆的聊天智能体。", "updated_round": 0}}
    ag._last_wakeup_summary = ""
    ag._rag_ready = False
    ag._last_recall_block = ""
    ag._receipt = ""
    ctx = ag._build_wakeup_ctx([])
    assert "完全自主" not in ctx and "数字生命" not in ctx
    assert "## 输出格式" not in ctx and "## 其他命令" not in ctx
    assert "【笔记本·事实层】" in ctx
    assert "【当前任务】" in ctx


def test_flat_note_command_writes_notebook():
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag._notebook = {}
    ag._flat_round = 1
    ag._last_wakeup_actions = []
    out = ag._execute_ahi_command("@note", ["state", "身份: 我是林深"])
    assert "身份" in ag._notebook
    assert ag._notebook["身份"]["kind"] == "state"
    assert ag._notebook["身份"]["content"] == "我是林深"
    assert "已更新" in out


def test_flat_recall_command_stashes_block():
    m = _load_flat_module()
    ag = m.FlatRAGAgent()
    ag._last_wakeup_actions = []
    ag._last_recall_block = ""
    ag._rag_block = lambda q: f"（检索 {q}）"
    out = ag._execute_ahi_command("@recall", ["身份"])
    assert ag._last_recall_block == "（检索 身份）"
    assert "身份" in out



def test_llm_client_complete_is_oneshot(monkeypatch):
    from sdk.llm_client import LLMClient
    lc = LLMClient({"api_base": "http://127.0.0.1:9/v1", "api_key": "x", "model": "m"})
    lc.messages = [{"role": "system", "content": "sys"}]
    seen = {}

    def fake_complete(messages):
        seen["m"] = list(messages)
        return "ok"

    monkeypatch.setattr(lc, "_complete", fake_complete)
    assert lc.complete("S", "U") == "ok"
    assert seen["m"] == [{"role": "system", "content": "S"},
                         {"role": "user", "content": "U"}]
    # 一次性调用不得污染滚动历史
    assert lc.messages == [{"role": "system", "content": "sys"}]


def test_llm_client_send_message_keeps_rolling(monkeypatch):
    from sdk.llm_client import LLMClient
    lc = LLMClient({"api_base": "http://127.0.0.1:9/v1", "api_key": "x", "model": "m"})
    lc.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr(lc, "_complete", lambda messages: "答复")
    assert lc.send_message("你好") == "答复"
    assert lc.messages == [{"role": "system", "content": "sys"},
                           {"role": "user", "content": "你好"},
                           {"role": "assistant", "content": "答复"}]
