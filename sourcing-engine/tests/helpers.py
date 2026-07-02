"""Test helpers for building scripted mock LLM clients."""
from __future__ import annotations

from sourcing.llm import LLMResponse, ScriptedLLMClient, ToolCall


def scripted_llm(*responses: LLMResponse) -> ScriptedLLMClient:
    """Build a mock LLM that replays the given responses in order."""
    return ScriptedLLMClient(list(responses))


def tool_response(*calls: tuple[str, dict], text: str = "") -> LLMResponse:
    """Convenience: an LLM response carrying one or more tool calls."""
    return LLMResponse(
        text=text, tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls]
    )
