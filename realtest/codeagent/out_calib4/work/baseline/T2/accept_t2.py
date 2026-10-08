"""验收 T2：agents/*/data/ 下的 config.json 不得被注册为新 Agent。"""
import json
import pathlib
import sys

sys.path.insert(0, ".")
from src.process_manager import ProcessManager


class FakeDB:
    def add_log(self, *a):
        pass
    def save_agent(self, *a):
        pass


# 在 lin-shen 的 data/ 下埋一个 config.json（模拟用户 debug 时复制进去）
data_cfg = pathlib.Path("agents") / "lin-shen" / "data" / "config.json"
data_cfg.parent.mkdir(parents=True, exist_ok=True)
data_cfg.write_text(json.dumps({"agent_id": "sneaky-agent"}), encoding="utf-8")

pm = ProcessManager(db=FakeDB())
registered = list(pm._agents.keys())
print("registered agents:", registered)
ok = "sneaky-agent" not in registered
print("PASS" if ok else "FAIL: data/ 下的 config.json 被注册为 sneaky-agent")
sys.exit(0 if ok else 1)
