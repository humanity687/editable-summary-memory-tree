import os
from typing import Any, Dict, List, Optional, Union

from openai import OpenAI, AzureOpenAI


class LLMClient:
    """OpenAI-compatible LLM client. Supports Ollama, Azure, and any OpenAI-compatible API."""

    def __init__(self, config: Dict[str, Any] = None, **overrides: Any):
        config = config or {}
        config.update(overrides)

        self.api_key = config.get("api_key") or os.getenv("OPENAI_API_KEY") or "ollama"
        self.api_base = config.get("api_base", "http://localhost:11434/v1")
        self.api_type = config.get("api_type")
        self.api_version = config.get("api_version")
        self.timeout = int(config.get("timeout", 60))

        if self.api_type:
            os.environ["OPENAI_API_TYPE"] = self.api_type
        if self.api_version:
            os.environ["OPENAI_API_VERSION"] = self.api_version

        self.client = self._create_client()

        self.model = config.get("model", "llama2")
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 1024))
        self.thinking = config.get("thinking", False)
        self.top_p = config.get("top_p")

        self.system_prompt = config.get("system_prompt", "You are a helpful assistant.")
        self.messages: List[Dict[str, str]] = []

    def _create_client(self) -> Union[OpenAI, AzureOpenAI]:
        if self.api_type == "azure":
            if not self.api_version:
                raise ValueError("api_version is required for Azure OpenAI")
            return AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.api_base,
                api_version=self.api_version,
                timeout=self.timeout,
            )
        return OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout,
        )

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = prompt
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})

    def clear_context(self) -> None:
        self.messages = []
        self.set_system_prompt(self.system_prompt)

    def get_context(self) -> List[Dict[str, str]]:
        return list(self.messages)

    # 上下文自动压缩阈值（消息条数）
    _MAX_MESSAGES = 120
    _KEEP_RECENT = 40

    def _auto_compress(self):
        """保留 system prompt + 最近 N 条消息，超出部分替换为摘要"""
        if len(self.messages) <= self._MAX_MESSAGES:
            return
        system_msg = self.messages[0] if self.messages and self.messages[0]["role"] == "system" else None
        recent = self.messages[-(self._KEEP_RECENT):]
        removed = self.messages[1:-(self._KEEP_RECENT)] if system_msg else self.messages[:-(self._KEEP_RECENT)]
        summary_lines = []
        for m in removed[-6:]:
            role = m.get("role", "?")
            snippet = m.get("content", "")[:80].replace("\n", " ")
            summary_lines.append(f"[{role}]: {snippet}")
        summary = ("[上下文摘要] 以下为早期对话梗概（共移除{}条消息）：\n".format(len(removed))
                   + "\n".join(summary_lines))
        if system_msg:
            self.messages = [system_msg, {"role": "assistant", "content": summary}] + recent
        else:
            self.messages = [{"role": "assistant", "content": summary}] + recent

    def send_message(self, content: str, role: str = "user") -> str:
        # 去重：与 v1 一致，相同 user 消息不重复发送
        for existing_msg in self.messages:
            if existing_msg.get("role") == "user" and existing_msg.get("content") == content:
                return ""

        self.messages.append({"role": role, "content": content})
        self._auto_compress()

        kwargs = {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p

        response = self.client.chat.completions.create(**kwargs)

        reply = response.choices[0].message.content or ""
        self.messages.append({"role": "assistant", "content": reply})
        return reply
