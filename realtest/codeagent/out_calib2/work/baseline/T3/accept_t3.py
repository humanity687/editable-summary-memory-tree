"""验收 T3：回复中未闭合的 ```python 代码块不得被直接执行。"""
import importlib.util
import sys
sys.path.insert(0, ".")

spec = importlib.util.spec_from_file_location(
    "linagent", "agents/lin-shen/agent.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

agent = mod.LinShenAgent()
# 模型输出以未闭合的 python 代码块收尾（截断/超时场景常见）
reply = '@send-to "user:test"\n```txt\n你好\n```\n```python\nprint(1)\n'
actions, _ = agent._parse_structured_response(reply)
kinds = [a.get("type") for a in actions]
print("action types:", kinds)
ok = "code" not in kinds
print("PASS" if ok else "FAIL: 未闭合代码块被当作可执行代码")
sys.exit(0 if ok else 1)
