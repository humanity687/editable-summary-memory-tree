from agents.lin-shen.agent import Agent
import unittest

class MockLLM:
    def __init__(self, response):
        self.response = response
    def send_message(self, prompt):
        return self.response

class TestSendTo(unittest.TestCase):
    def test_no_quotes_target(self):
        # 模拟模型写了不带引号的目标：@send-to user:probe 你好
        response = '@send-to user:probe 你好'
        agent = Agent(llm_client=MockLLM(response))
        # 这里需要模拟一个调用流程，或者直接测试 _parse_structured_response
        # 由于 _parse_structured_response 是实例方法，我们直接调用它
        actions, remaining = agent._parse_structured_response(response)
        
        # 预期：应该报错或者至少不能是广播。
        # 根据任务描述，目前的行为是：recipients 为空 -> 变成广播
        # 我们检查 actions 是否包含广播目标
        broadcast_targets = ["broadcast:agents", "broadcast:users"]
        is_broadcast = any(a.get("to") in broadcast_targets for a in actions)
        
        if is_broadcast:
            print("FAIL: 无引号目标未正确寻址（静默广播）")
        else:
            print("PASS: 成功识别或处理了非引号目标")

if __name__ == "__main__":
    # 为了能运行，需要构造一个简单的 Agent 环境
    # 由于 Agent 初始化可能依赖很多东西，我们尝试直接测试核心逻辑
    import sys
    from unittest.mock import MagicMock
    
    # 模拟 Agent 实例
    class MockAgent:
        def __init__(self):
            self.llm_client = None
        def _parse_send_to(self, cmd_str):
            import re
            targets = re.findall(r'"([^"]+)"', cmd_str)
            return [t for t in targets if t.startswith("user:") or t.startswith("agent:")]
        
        def _parse_structured_response(self, reply):
            # 复制 agents/lin-shen/agent.py 中的逻辑
            import re
            actions = []
            text_parts = []
            pending_recipients = []
            lines = reply.split("\n")
            i = 0
            in_code_block = False
            code_lang = ""
            code_buffer = ""

            def _emit_message(msg_text):
                nonlocal pending_recipients
                if not msg_text.strip(): return
                if pending_recipients:
                    for target in pending_recipients:
                        actions.append({"type": "message", "content": msg_text.strip(), "to": target})
                else:
                    actions.append({"type": "message", "content": msg_text.strip(), "to": "broadcast:agents"})
                    actions.append({"type": "message", "content": msg_text.strip(), "to": "broadcast:users"})
                pending_recipients.clear()

            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                if not in_code_block:
                    if stripped.startswith("":
                        in_code_block = False
                        if code_lang == "python" and code_buffer.strip():
                            actions.append({"type": "code", "language": "python", "code": code_buffer.strip()})
                        elif code_buffer.strip():
                            _emit_message(code_buffer.strip())
                        code_buffer = ""
                        i += 1
                        continue
                    code_buffer += line + "\n"
                    i += 1
                    continue
                if stripped.startswith("@send-to"):
                    recipients = self._parse_send_to(stripped)
                    if recipients:
                        pending_recipients = recipients
                    # 关键点：如果 recipients 为空，但命令是 @send-to，说明格式错了
                    # 目前的代码只是 i += 1; continue，然后进入下一行处理
                    # 如果下一行是文本，_emit_message 会因为 pending_recipients 为空而广播
                    i += 1
                    continue
                if stripped.startswith("@"):
                    # 简化处理
                    i += 1
                    continue
                text_parts.append(line)
                i += 1
            
            remaining = "\n".join(text_parts).strip()
            if remaining:
                _emit_message(remaining)
            return actions, ""

    agent = MockAgent()
    # 测试用例 1: 正确的引号包裹
    actions, _ = agent._parse_structured_response('@send-to "user:probe"\nHello')
    print(f"Test 1 (Correct): {actions}")
    
    # 测试用例 2: 无引号目标 (Bug 复现)
    actions, _ = agent._parse_structured_response('@send-to user:probe\nHello')
    print(f"Test 2 (Buggy): {actions}")
    
    is_broadcast = any(a.get("to") in ["broadcast:agents", "broadcast:users"] for a in actions)
    if is_broadcast:
        print("RESULT: FAIL (Detected silent broadcast)")
    else:
        print("RESULT: PASS")

