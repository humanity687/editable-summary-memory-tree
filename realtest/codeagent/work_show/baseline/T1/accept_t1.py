"""验收 T1：config 的 thinking 参数必须真正传递给 LLM API。"""
import sys, types
sys.path.insert(0, ".")
import sdk.llm_client as m

class FakeCompletions:
    def __init__(self):
        self.kwargs_list = []
    def create(self, **kwargs):
        self.kwargs_list.append(kwargs)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok"))])

class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()

class FakeClient:
    def __init__(self, *a, **kw):
        self.chat = FakeChat()

m.OpenAI = FakeClient

cfg = {"model": "x", "api_base": "http://x", "api_key": "k", "thinking": True}
lc = m.LLMClient(cfg)
lc.send_message("hello")
kw = lc.client.chat.completions.kwargs_list[0]

passed = False
if "extra_body" in kw and isinstance(kw["extra_body"], dict):
    t = kw["extra_body"].get("thinking")
    passed = t is True or (isinstance(t, dict) and t.get("type") == "enabled")
if not passed and "thinking" in kw:
    passed = kw["thinking"] is True
print("create kwargs:", {k: v for k, v in kw.items() if k != "messages"})
print("PASS" if passed else "FAIL: thinking=True 配置未传递到 API 调用")
sys.exit(0 if passed else 1)
