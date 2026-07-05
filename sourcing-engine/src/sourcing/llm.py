"""LLMClient — a thin, injectable wrapper over a tool-calling chat LLM (plan §2, §6).

Two live implementations behind one protocol:

* :class:`AnthropicLLMClient` — the Claude Messages API (the default provider). Fast,
  reliable tool calling and JSON output; the model ids come from ``config`` (default
  ``claude-opus-4-8``).
* :class:`OllamaLLMClient` — a local Ollama server (``/api/chat``). Kept as an
  offline/self-hosted fallback (``LLM_PROVIDER=ollama``).

The rest of the engine depends only on the :class:`LLMClient` protocol, so unit tests
inject a scripted mock and need no live server or API key.
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


# ---------------------------------------------------------------------------
# Anthropic (Claude Messages API) — the default provider
# ---------------------------------------------------------------------------


def _to_anthropic_tools(tools: list[dict] | None) -> list[dict]:
    """Translate Ollama/OpenAI function schemas to Anthropic tool definitions.

    ``{"type": "function", "function": {"name", "description", "parameters"}}``
    → ``{"name", "description", "input_schema"}``.
    """
    out: list[dict] = []
    for t in tools or []:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Translate the engine's provider-agnostic history to Anthropic's message shape.

    The internal history (see ``BuyBoxAgent.step``) is user/assistant messages plus
    ``{"role": "tool", ...}`` results, where an assistant turn carries
    ``tool_calls=[{"function": {"name", "arguments"}}]`` and its results follow as
    separate ``tool`` messages in the SAME order. Anthropic instead needs matching
    ``tool_use`` / ``tool_result`` ids and all results for one turn in a single user
    message — so we synthesize ids positionally (or reuse a stored ``id``) and coalesce
    consecutive tool results.
    """
    out: list[dict] = []
    pending_results: list[dict] = []
    pending_ids: list[str] = []
    counter = 0

    def _flush() -> None:
        nonlocal pending_results
        if pending_results:
            out.append({"role": "user", "content": pending_results})
            pending_results = []

    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            tid = msg.get("tool_call_id") or (
                pending_ids.pop(0) if pending_ids else f"toolu_{counter}"
            )
            counter += 1
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": str(msg.get("content", "")),
                }
            )
            continue

        _flush()  # emit any collected tool_results before the next non-tool turn
        if role == "assistant":
            blocks: list[dict] = []
            text = msg.get("content") or ""
            if text.strip():
                blocks.append({"type": "text", "text": text})
            pending_ids = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or tc
                tid = tc.get("id") or f"toolu_{counter}"
                counter += 1
                pending_ids.append(tid)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tid,
                        "name": fn.get("name", ""),
                        "input": fn.get("arguments") or {},
                    }
                )
            if not blocks:  # Anthropic rejects empty assistant content
                blocks.append({"type": "text", "text": "(no content)"})
            out.append({"role": "assistant", "content": blocks})
        else:  # user
            out.append({"role": "user", "content": msg.get("content", "")})

    _flush()
    return out


class AnthropicLLMClient:
    """Tool-calling chat against the Anthropic (Claude) Messages API.

    Implements the same :class:`LLMClient` protocol as :class:`OllamaLLMClient` by
    translating the engine's provider-agnostic message/tool format to Anthropic's
    Messages API and mapping ``tool_use`` response blocks back to :class:`ToolCall`.
    Adaptive thinking is intentionally left off — the engine's history is
    text + tool-call only, so there are no thinking blocks to preserve across turns.
    """

    def __init__(
        self,
        api_key: str = "",
        timeout: float = 120.0,
        max_tokens: int = 4096,
        client: Any = None,
    ):
        self._max_tokens = max_tokens
        if client is not None:  # tests inject a fake
            self._client = client
            return
        import anthropic

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set but LLM_PROVIDER='anthropic'. "
                "Add ANTHROPIC_API_KEY to .env or set LLM_PROVIDER=ollama."
            )
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        format: str | dict | None = None,
    ) -> LLMResponse:
        sys_text = system
        if format:  # no native JSON param on Anthropic — reinforce via the system prompt
            sys_text = (
                system
                + "\n\nRespond with ONLY a single valid JSON object. "
                "Do not wrap it in markdown code fences or add any prose."
            ).strip()

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "system": sys_text,
            "messages": _to_anthropic_messages(messages),
        }
        anth_tools = _to_anthropic_tools(tools)
        if anth_tools:
            kwargs["tools"] = anth_tools

        resp = self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                calls.append(ToolCall(name=block.name, arguments=dict(args), id=block.id))
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=calls)


# ---------------------------------------------------------------------------
# Ollama — local/self-hosted fallback provider
# ---------------------------------------------------------------------------


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

    resp = llm.chat(
        model=model, system=system, messages=[{"role": "user", "content": user}], format="json"
    )
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


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider == "anthropic":
        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout,
            max_tokens=settings.llm_max_tokens,
        )
    return OllamaLLMClient(host=settings.ollama_host, timeout=settings.ollama_timeout)
