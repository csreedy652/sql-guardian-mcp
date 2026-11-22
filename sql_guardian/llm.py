"""Minimal LLM client for any OpenAI-compatible chat endpoint.

Works with OpenAI, Azure OpenAI-compatible gateways, Databricks model serving,
vLLM and local Ollama (`OPENAI_BASE_URL=http://localhost:11434/v1`).
Uses only the standard library so the project has no hard SDK dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, temperature: float = 0.0, timeout: float = 60.0):
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        return payload["choices"][0]["message"]["content"]


class ScriptedClient:
    """Deterministic stand-in for tests and demos: returns canned replies in order."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.replies:
            raise RuntimeError("ScriptedClient ran out of replies")
        return self.replies.pop(0)
