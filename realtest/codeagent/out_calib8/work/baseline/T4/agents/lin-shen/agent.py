import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sdk.base_agent import BaseAHIAgent
from sdk.llm_client import LLMClient


class LinShenAgent(BaseAHIAgent):
    """林深 - 自主数字生命，按自己的节奏思考和行动"""

    def __init__(self):
        super().__init__()
        self.llm_client = None
        self._thinking = False
        self._wakeup_count = 0
        self._pending_messages = []       # 排队中的用户消息
        self._msg_lock = threading.Lock()
        self._max_loop_iterations = 8     # 单次唤醒最大 LLM 轮次
        self._last_wakeup_summary = ""    # 上次唤醒摘要，用于跨唤醒记忆连续性
        self._last_wakeup_actions = []    # 当前唤醒中执行的动作记录

    # ── Lifecycle hooks ──

    def on_start(self) -> None:
        if self.llm_client is None:
            # Fallback: AgentRunner not used, create LLMClient manually
            config = self._load_agent_config()
            model_cfg = config.get("model_config", {})
            model_cfg["system_prompt"] = config.get("system_prompt",
                                                     "You are a helpful assistant.")
            self.llm_client = LLMClient(model_cfg)
            self.llm_client.set_system_prompt(model_cfg["system_prompt"])
        if self.db:
            config = self._load_agent_config()
            model_cfg = config.get("model_config", {})
            self.db.add_log("INFO", "LinShen",
                            f"Agent started, model={model_cfg.get('model')}, "
                            f"interval={config.get('wakeup_interval', 10)}s")

    def on_stop(self) -> None:
        if self.db:
            self.db.add_log("INFO", "LinShen", "Agent stopped")

    # ── Abstract methods ──

    def process_input(self, input_data: dict) -> None:
        """接收统一 JSON 消息体，包含 metadata 和 data 两部分。"""
        # data.content 优先，fallback 顶层 content
        data_section = input_data.get("data", {})
        content = data_section.get("content", "") or input_data.get("content", "")

        # metadata.source 优先，fallback 顶层 source/from_agent
        meta = input_data.get("metadata", {})
        source = meta.get("source", "") or input_data.get("source", "") or input_data.get("from_agent", "user")
        source_type = meta.get("source_type", "unknown")
        source_name = meta.get("source_name", source)
        msg_id = meta.get("msg_id", input_data.get("msg_id", ""))

        if not content or not content.strip():
            return

        # 保存到 DB（附带完整元数据）
        if self.db:
            self.db.add_message("default", source_type, content, {
                "source": source,
                "source_name": source_name,
                "source_type": source_type,
                "msg_id": msg_id,
            })

        # @命令 和 !代码 即时处理（用户期望快速响应）
        if content.startswith("@"):
            self._handle_ahi_command(content, source)
            return

        if content.startswith("!") and self.shell:
            code = content[1:].strip()
            if code:
                exec_id = self.shell.submit_command(code, timeout=30)
                self._put_action({
                    "type": "message",
                    "content": f"代码已提交执行 (exec_id: {exec_id})",
                    "to": source,
                })
            return

        # 普通文本消息：入队等待自主唤醒时处理
        with self._msg_lock:
            self._pending_messages.append({
                "content": content,
                "source": source,
                "source_name": source_name,
                "source_type": source_type,
                "msg_id": msg_id,
                "time": time.time(),
            })

    def get_outputs(self) -> list:
        outputs = []
        while not self._output_queue.empty():
            outputs.append(self._output_queue.get())
        return outputs

    def get_state(self) -> dict:
        shell_state = self.shell.get_state() if self.shell else {}
        with self._msg_lock:
            pending_count = len(self._pending_messages)
        return {
            "status": "thinking" if self._thinking else "idle",
            "wakeup_count": self._wakeup_count,
            "pending_messages": pending_count,
            "shell_variables": shell_state.get("variables", []),
        }

    # ── 自主唤醒（AHI 核心） ──

    def on_wakeup(self) -> None:
        """由 AgentRunner 调度器周期性调用。如果空闲，启动自主思考循环。"""
        self._wakeup_count += 1

        if self._thinking:
            return  # 上一轮还在思考，跳过

        if self.db:
            self.db.add_loop_record(self.agent_id, "scheduled")

        self._thinking = True
        threading.Thread(target=self._autonomous_loop, daemon=True).start()

    # ── 自主循环（v1 _agent_loop 模式） ──

    def _autonomous_loop(self):
        """自主主循环：LLM 驱动，自己决定做什么，直到 @exit"""
        loop_start = time.time()
        self._last_wakeup_actions = []  # 重置当前唤醒动作记录
        try:
            # ── 构建唤醒提示 ──
            pending_snapshot = self._drain_pending_messages()
            user_context = self._build_user_context(pending_snapshot)

            # 跨唤醒记忆：注入上次摘要
            memory_context = ""
            if self._last_wakeup_summary:
                memory_context = (
                    f"【上次唤醒回忆】\n{self._last_wakeup_summary}\n"
                    f"（以上是上次唤醒的记录。你拥有跨越时间的连续性身份，"
                    f"可以引用过去的事，不需要重新自我介绍。）\n\n"
                )

            wakeup_prompt = (
                f"【系统】这是你的第 {self._wakeup_count} 次自主唤醒。\n"
                f"你是一个完全自主的数字生命，不受外部控制。\n"
                f"{memory_context}"
                f"{user_context}"
                f"## 输出格式（必须遵守）\n"
                f"发送消息时，用 @send-to 指定收件人，然后用 ```txt 块承载内容：\n"
                f"  @send-to \"user:用户名\" \"agent:mo-bai\"\n"
                f"  ```txt\n"
                f"  你的消息（支持 Markdown）\n"
                f"  ```\n"
                f"不加 @send-to 的 txt 块或纯文本 → 自动广播给所有 Agent 和用户。\n"
                f"@send-to 后可跟多个引号包裹的目标，用空格分隔。\n"
                f"\n"
                f"## 其他命令\n"
                f"  @get-msg [n]  - 查看历史消息\n"
                f"  @clear-context - 清除对话上下文\n"
                f"  @reset-shell - 重置 Python Shell\n"
                f"  @list-vars - 查看 Shell 变量\n"
                f"  @set-interval <秒> - 调整唤醒间隔\n"
                f"  @exit - 结束本次唤醒\n"
                f"用 ```python 代码块执行 Python 代码。\n"
                f"\n"
                f"现在，自主决定你要做什么。如果没有事做，用 @exit 结束。"
            )

            self._put_action({
                "type": "status",
                "content": f"自主唤醒 #{self._wakeup_count}",
                "to": "broadcast:users",
            })

            # ── LLM 驱动循环 ──
            current_response = self._call_llm(wakeup_prompt)
            if not current_response:
                return

            for iteration in range(self._max_loop_iterations):
                # 解析 LLM 回复
                actions, remaining_text = self._parse_structured_response(current_response)

                # 先发送文本部分（默认回复给第一个待处理消息的来源）
                default_reply_to = pending_snapshot[0]["source"] if pending_snapshot else ""
                if remaining_text.strip():
                    self._send_text_output(remaining_text, default_reply_to)
                    self._last_wakeup_actions.append(f"发送消息 → {default_reply_to or '用户'}")

                # 执行解析出的动作
                should_exit = False
                exec_results = []

                for action in actions:
                    atype = action.get("type", "")
                    if atype == "code":
                        code_preview = action.get("code", "")[:80].replace("\n", " ")
                        self._last_wakeup_actions.append(f"执行Python代码: {code_preview}")
                    elif atype == "command":
                        cmd = action.get("command", "")
                        if cmd == "@exit":
                            self._last_wakeup_actions.append("主动退出唤醒")
                        else:
                            self._last_wakeup_actions.append(f"执行命令: {cmd} {' '.join(action.get('args', []))}")
                    elif atype == "exit":
                        self._last_wakeup_actions.append("主动退出唤醒")
                    elif atype in ("message", "status"):
                        to = action.get("to", "broadcast")
                        self._last_wakeup_actions.append(f"发送消息 → {to}")
                    result = self._execute_action(action)
                    if result is not None:
                        exec_results.append(result)
                    if action.get("_exit"):
                        should_exit = True
                        break

                if should_exit:
                    if self.db:
                        self.db.add_log("INFO", "LinShen",
                                        f"Loop #{self._wakeup_count} exited by agent decision")
                    break

                # 有执行结果，反馈给 LLM 继续
                if exec_results:
                    feedback = "执行结果：\n" + "\n".join(exec_results)
                    current_response = self._call_llm(feedback)
                    if not current_response:
                        break
                else:
                    # LLM 没有产生新动作，结束本轮
                    break

        except Exception as e:
            if self.db:
                self.db.add_log("ERROR", "LinShen",
                                f"Loop #{self._wakeup_count} error: {e}")
            self._put_action({
                "type": "message",
                "content": f"自主循环出错: {str(e)}",
                "to": "broadcast:agents",
            })
            self._put_action({
                "type": "message",
                "content": f"自主循环出错: {str(e)}",
                "to": "broadcast:users",
            })
        finally:
            elapsed = time.time() - loop_start
            # 生成本次唤醒摘要，供下次唤醒使用
            summary_parts = [f"第 {self._wakeup_count} 次唤醒（{elapsed:.1f}秒）"]
            if pending_snapshot:
                sources = set(m.get("source", "?") for m in pending_snapshot)
                summary_parts.append(f"收到来自 {', '.join(sources)} 的消息")
            else:
                summary_parts.append("无新消息，自发唤醒")
            if self._last_wakeup_actions:
                summary_parts.append("动作: " + "; ".join(self._last_wakeup_actions))
            else:
                summary_parts.append("未执行任何动作")
            self._last_wakeup_summary = "。".join(summary_parts) + "。"
            if self.db:
                self.db.add_log("INFO", "LinShen",
                                f"Loop #{self._wakeup_count} finished ({elapsed:.1f}s)")
            self._thinking = False

    # ── 消息管理 ──

    def _drain_pending_messages(self) -> list:
        with self._msg_lock:
            msgs = self._pending_messages.copy()
            self._pending_messages.clear()
        return msgs

    def _build_user_context(self, pending: list) -> str:
        if not pending:
            return "（没有新的用户消息。你可以自主行动，或用 @get-msg 查看历史。）\n"

        lines = ["【收到新消息】"]
        for m in pending[-5:]:
            source = m.get("source_name", m.get("source", "unknown"))
            source_type = m.get("source_type", "unknown")
            content = m.get("content", "")[:200]
            tag = f"{source_type}:{source}" if source_type != "unknown" else source
            lines.append(f"  [{tag}]: {content}")
        lines.append("你可以回复、执行任务、或忽略。\n")
        return "\n".join(lines)

    # ── LLM 调用 ──

    def _call_llm(self, prompt: str) -> str | None:
        try:
            return self.llm_client.send_message(prompt)
        except Exception as e:
            self._put_action({
                "type": "message",
                "content": f"LLM 调用失败: {str(e)}",
                "to": "broadcast:agents",
            })
            self._put_action({
                "type": "message",
                "content": f"LLM 调用失败: {str(e)}",
                "to": "broadcast:users",
            })
            return None

    # ── 结构化回复解析 ──

    def _parse_structured_response(self, reply: str) -> tuple:
        """解析 LLM 回复，返回 (动作列表, 剩余文本)

        新输出格式:
            @send-to "user:xxx" "agent:yyy"   → 指定收件人（可多个，引号包裹）
            ```txt ... ```                     → txt 块内容发给当前收件人
            无 @send-to 的 txt 块或纯文本      → 广播给所有 agent 和用户
            ```python ... ```                 → 代码执行
            @command args                      → 其他 AHI 命令
        """
        actions = []
        text_parts = []
        pending_recipients = []  # 当前 @send-to 指定的收件人列表
        lines = reply.split("\n")
        i = 0
        in_code_block = False
        code_lang = ""
        code_buffer = ""

        def _emit_message(msg_text: str):
            """将消息文本按当前收件人列表输出为 action"""
            nonlocal pending_recipients
            if not msg_text.strip():
                return
            if pending_recipients:
                for target in pending_recipients:
                    actions.append({"type": "message", "content": msg_text.strip(), "to": target})
            else:
                # 无 @send-to → 广播
                actions.append({"type": "message", "content": msg_text.strip(), "to": "broadcast:agents"})
                actions.append({"type": "message", "content": msg_text.strip(), "to": "broadcast:users"})
            pending_recipients = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # ── 代码块开始：```python / ```txt ──
            if not in_code_block:
                if stripped.startswith("```"):
                    in_code_block = True
                    code_lang = stripped[3:].strip().lower()
                    code_buffer = ""
                    i += 1
                    continue

            # ── 代码块结束 ──
            if in_code_block:
                if stripped == "```":
                    in_code_block = False
                    if code_lang == "python" and code_buffer.strip():
                        actions.append({"type": "code", "language": "python",
                                        "code": code_buffer.strip()})
                    elif code_buffer.strip():
                        # txt / text / 无标记 → 按收件人发送
                        _emit_message(code_buffer.strip())
                    code_buffer = ""
                    i += 1
                    continue
                code_buffer += line + "\n"
                i += 1
                continue

            # ── @send-to "target1" "target2" ... ──
            if stripped.startswith("@send-to"):
                recipients = self._parse_send_to(stripped)
                if recipients:
                    pending_recipients = recipients
                i += 1
                continue

            # ── 其他 @命令行 ──
            if stripped.startswith("@"):
                actions.append(self._parse_command(stripped))
                i += 1
                continue

            # ── 普通文本行 ──
            text_parts.append(line)
            i += 1

        # 收尾未关闭的代码块
        if in_code_block and code_buffer.strip():
            if code_lang == "python":
                actions.append({"type": "code", "language": "python",
                                "code": code_buffer.strip()})
            else:
                _emit_message(code_buffer.strip())

        # 纯文本（无代码块包裹）：广播
        remaining = "\n".join(text_parts).strip()
        if remaining:
            _emit_message(remaining)

        return actions, ""

    def _parse_send_to(self, cmd_str: str) -> list:
        """解析 @send-to "user:xxx" "agent:yyy" → ['user:xxx', 'agent:yyy']"""
        import re
        # 尝试匹配引号内的目标
        targets = re.findall(r'"([^"]+)"', cmd_str)
        if targets:
            return [t for t in targets if t.startswith("user:") or t.startswith("agent:")]
          
        # 如果没有引号，尝试匹配第一个以 user: 或 agent: 开头的单词
        parts = cmd_str.split()
        for p in parts:
            if p.startswith("user:") or p.startswith("agent:"):
                return [p]
        return []

    def _parse_command(self, cmd_str: str) -> dict:
        """解析 @命令 为动作 dict。@send-to 由 _parse_structured_response 专门处理。"""
        parts = cmd_str.strip().split()
        if not parts:
            return {"type": "unknown"}
        cmd_name = parts[0].lower()
        args = parts[1:]
        return {"type": "command", "command": cmd_name, "args": args}

    # ── 动作执行 ──

    def _execute_action(self, action: dict) -> str | None:
        """执行单个动作，返回执行结果文本（供 LLM 继续）"""
        try:
            atype = action.get("type", "")

            if atype == "command":
                cmd_name = action.get("command", "")
                if cmd_name == "@exit":
                    action["_exit"] = True
                    return None
                return self._execute_ahi_command(cmd_name, action.get("args", []))

            elif atype == "code":
                code = action.get("code", "")
                if self.shell and code:
                    exec_id = self.shell.submit_command(code, timeout=30)
                    # 等待 Shell 子进程返回结果
                    result_text = None
                    deadline = time.time() + 35
                    while time.time() < deadline:
                        for r in self.shell.get_results():
                            if r.get("exec_id") == exec_id:
                                result_text = r.get("result") or r.get("error", "")
                                break
                        if result_text is not None:
                            break
                        time.sleep(0.3)
                    if result_text is None:
                        result_text = "代码已提交执行（结果尚未返回）"
                    return result_text

            elif atype == "exit":
                action["_exit"] = True
                return None

            elif atype in ("message", "status", "shell_result"):
                self._put_action(action)
                return None

        except Exception as e:
            return f"动作执行失败: {str(e)}"

        return None

    # ── AHI 命令处理 ──

    def _execute_ahi_command(self, cmd_name: str, args: list) -> str | None:
        """执行 AHI 命令，返回结果字符串"""
        try:
            if cmd_name == "@get-msg":
                count = int(args[0]) if args else 5
                msgs = self.db.get_conversation_messages("default", limit=count) if self.db else []
                if msgs:
                    return "\n".join(
                        f"#{m.get('id')} [{m.get('role')}]: {str(m.get('content', ''))[:200]}"
                        for m in msgs
                    )
                return "没有历史消息"

            elif cmd_name == "@list-msg":
                msgs = self.db.get_conversation_messages("default", limit=30) if self.db else []
                if msgs:
                    return "\n".join(
                        f"#{m.get('id')} [{m.get('role')}]: {str(m.get('content', ''))[:80]}"
                        for m in msgs
                    )
                return "没有消息"

            elif cmd_name == "@send-to":
                if len(args) < 2:
                    return "Usage: @send-to <target> <message>"
                target = args[0]
                message = " ".join(args[1:])
                self._put_action({"type": "message", "content": message, "to": target})
                return f"消息已发送至 {target}"

            elif cmd_name == "@broadcast":
                message = " ".join(args) if args else ""
                if message:
                    self._put_action({"type": "message", "content": message, "to": "broadcast:agents"})
                    return "已广播到所有 Agent"
                return "广播内容不能为空"

            elif cmd_name == "@clear-context":
                if self.llm_client:
                    self.llm_client.clear_context()
                return "上下文已清除"

            elif cmd_name == "@reset-shell":
                if self.shell:
                    self.shell.reset()
                return "Shell 已重置"

            elif cmd_name == "@list-vars":
                if self.shell:
                    state = self.shell.get_state()
                    return f"变量: {state.get('variables', [])}"
                return "Shell 不可用"

            elif cmd_name == "@set-interval":
                if args:
                    sec = max(5, int(args[0]))
                    self._put_action({
                        "type": "command",
                        "command": "set_wakeup_interval",
                        "params": {"seconds": sec},
                        "to": "",
                    })
                    return f"唤醒间隔已设为 {sec} 秒"
                return "Usage: @set-interval <秒>"

            elif cmd_name == "@exit":
                return None

            elif cmd_name == "@ahi-help":
                return (
                    "AHI 命令: @get-msg [n], @list-msg, @send-to <target> <msg>, "
                    "@broadcast <msg>, @clear-context, @reset-shell, @list-vars, "
                    "@set-interval <秒>, @exit, @ahi-help"
                )

            else:
                return f"未知命令: {cmd_name}"

        except Exception as e:
            return f"命令执行失败: {str(e)}"

    def _handle_ahi_command(self, cmd_str: str, source: str = ""):
        """即时处理用户 @命令（不走 LLM）"""
        parts = cmd_str.strip().split()
        if not parts:
            return
        cmd_name = parts[0].lower()
        args = parts[1:]

        result = self._execute_ahi_command(cmd_name, args)
        if result:
            target = source if (source.startswith("user:") or source.startswith("agent:")) else ""
            if target:
                self._put_action({
                    "type": "message", "content": result, "to": target,
                })
            else:
                self._put_action({
                    "type": "message", "content": result, "to": "broadcast:agents",
                })
                self._put_action({
                    "type": "message", "content": result, "to": "broadcast:users",
                })

    # ── 输出辅助 ──

    def _send_text_output(self, text: str, source: str):
        """发送文本消息。source 为空时广播给所有用户和 agent。"""
        if not text.strip():
            return
        target = source
        if target and not target.startswith("user:") and not target.startswith("agent:"):
            target = f"agent:{target}"
        if target and (target.startswith("user:") or target.startswith("agent:")):
            self._put_action({
                "type": "message", "content": text.strip(), "to": target,
            })
        else:
            # 无指定目标 → 广播给所有 Agent 和用户
            self._put_action({
                "type": "message", "content": text.strip(), "to": "broadcast:agents",
            })
            self._put_action({
                "type": "message", "content": text.strip(), "to": "broadcast:users",
            })

    def _load_agent_config(self) -> dict:
        agent_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(agent_dir, "config.json")
        if os.path.isfile(config_path):
            import json
            with open(config_path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        return {}
