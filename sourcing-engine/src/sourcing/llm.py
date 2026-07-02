"""LLMClient — a thin, injectable wrapper over a tool-calling chat LLM (plan §2, §6).

The default implementation targets a local **Ollama** server (`/api/chat`) with a
model that supports tool calling (e.g. ``gpt-oss:20b``, ``llama3.1``, ``qwen2.5``).
The agent depends only on the :class:`LLMClient` protocol, so unit tests inject a
scripted mock and need no live server.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import Settings, get_settings


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def asked_question(self) -> bool:
        """Heuristic: a clarifying question contains a '?' and no tool call."""
        return "?" in self.text and not self.tool_calls


class LLMClient(Protocol):
    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        format: str | dict | None = None,
    ) -> LLMResponse: ...


class OllamaLLMClient:
    """Tool-calling chat against a local Ollama server."""

    def __init__(self, host: str, timeout: float = 300.0):
        self.host = host.rstrip("/")
        self._timeout = timeout

    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        format: str | dict | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if format is not None:
            payload["format"] = format  # "json" or a JSON schema — forces structured output

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{self.host}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        msg = data.get("message", {})
        calls = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json

                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
        return LLMResponse(text=msg.get("content", "") or "", tool_calls=calls)


class ScriptedLLMClient:
    """A mock LLMClient that replays a fixed list of :class:`LLMResponse` per call.

    Used by the unit suite to drive the agent deterministically without a server.
    """

    def __init__(self, script: list[LLMResponse]):
        self._script = list(script)
        self.calls: list[dict] = []

    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        format: str | dict | None = None,
    ) -> LLMResponse:
        self.calls.append({"model": model, "messages": list(messages), "format": format})
        if not self._script:
            return LLMResponse(text="(no more scripted responses)")
        return self._script.pop(0)


def complete_json(
    llm: LLMClient,
    model: str,
    system: str,
    user: str,
) -> dict:
    """Call the LLM in JSON mode and parse the result into a dict.

    Tolerant of models that wrap JSON in prose/code fences — extracts the first
    balanced ``{...}`` object. Returns ``{}`` on unparseable output.
    """
    import json
    import re

    resp = llm.chat(model=model, system=system, messages=[{"role": "user", "content": user}], format="json")
    text = (resp.text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


def complete_text(llm: LLMClient, model: str, system: str, user: str) -> str:
    """Plain-text completion (no JSON grammar constraint).

    Preferred over ``complete_json`` for large/list outputs on a local CPU model:
    grammar-constrained JSON decoding is pathologically slow there, while plain
    text is much faster. The caller parses the result.
    """
    resp = llm.chat(model=model, system=system, messages=[{"role": "user", "content": user}])
    return (resp.text or "").strip()


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    return OllamaLLMClient(host=settings.ollama_host, timeout=settings.ollama_timeout)
