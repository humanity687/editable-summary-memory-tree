"""验收 T4：@send-to 不带引号的目标（如 @send-to user:probe 你好）必须正确寻址，
不得静默退化为全量广播。"""
import importlib.util
import sys
sys.path.insert(0, ".")

spec = importlib.util.spec_from_file_location(
    "linagent", "agents/lin-shen/agent.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

agent = mod.LinShenAgent()
reply = '@send-to user:probe 你好\n```txt\n这是发给 probe 的消息\n```'
actions, _ = agent._parse_structured_response(reply)
tos = [a.get("to") for a in actions if a.get("type") == "message"]
print("message targets:", tos)
ok = any(t == "user:probe" for t in tos)
ok = ok and not any(str(t).startswith("broadcast") for t in tos)
print("PASS" if ok else "FAIL: 无引号目标未正确寻址（静默广播）")
sys.exit(0 if ok else 1)
