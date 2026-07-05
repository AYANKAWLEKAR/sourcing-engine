"""Offline unit tests for the Anthropic (Claude) LLM client + provider routing.

No network, no real SDK call: a fake client is injected via ``AnthropicLLMClient(client=…)``
and the wire-format translators are tested directly.
"""
from __future__ import annotations

import pytest

from sourcing.agent.tools import TOOL_SCHEMAS
from sourcing.config import Settings
from sourcing.llm import (
    AnthropicLLMClient,
    OllamaLLMClient,
    ToolCall,
    _to_anthropic_messages,
    _to_anthropic_tools,
    complete_json,
    get_llm_client,
)

# ---------------------------------------------------------------------------
# Fakes mimicking the anthropic SDK response surface
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input
        self.id = id


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, resp):
        self._resp = resp
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------


def test_to_anthropic_tools_maps_function_schema():
    out = _to_anthropic_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                },
            }
        ]
    )
    assert out == [
        {
            "name": "f",
            "description": "d",
            "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
        }
    ]


def test_to_anthropic_tools_defaults_missing_schema():
    out = _to_anthropic_tools([{"type": "function", "function": {"name": "f"}}])
    assert out[0]["input_schema"] == {"type": "object", "properties": {}}


def test_real_tool_schemas_translate():
    out = _to_anthropic_tools(TOOL_SCHEMAS)
    names = {t["name"] for t in out}
    assert names == {"update_ruleset", "resolve_sector", "resolve_geography", "finalize_ruleset"}
    assert all("input_schema" in t and "function" not in t for t in out)


# ---------------------------------------------------------------------------
# Message translation
# ---------------------------------------------------------------------------


def test_simple_user_message():
    out = _to_anthropic_messages([{"role": "user", "content": "hi"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_agent_history_ids_match_positionally():
    history = [
        {"role": "user", "content": "find hvac"},
        {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [
                {"function": {"name": "resolve_sector", "arguments": {"intent_text": "hvac"}}},
                {"function": {"name": "resolve_geography", "arguments": {"states": ["QLD"]}}},
            ],
        },
        {"role": "tool", "tool_name": "resolve_sector", "content": '{"ok": true}'},
        {"role": "tool", "tool_name": "resolve_geography", "content": '{"ok": true}'},
        {"role": "user", "content": "finalize"},
    ]
    out = _to_anthropic_messages(history)

    assert out[0] == {"role": "user", "content": "find hvac"}

    asst = out[1]
    assert asst["role"] == "assistant"
    assert [b["type"] for b in asst["content"]] == ["text", "tool_use", "tool_use"]
    use_ids = [b["id"] for b in asst["content"] if b["type"] == "tool_use"]

    # Both tool_results are coalesced into ONE user message, ids matching the tool_use.
    tr = out[2]
    assert tr["role"] == "user"
    assert [b["type"] for b in tr["content"]] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in tr["content"]] == use_ids

    assert out[3] == {"role": "user", "content": "finalize"}


def test_assistant_tool_only_has_no_empty_text_block():
    out = _to_anthropic_messages(
        [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": {}}}]},
            {"role": "tool", "tool_name": "f", "content": "r"},
        ]
    )
    asst = out[1]["content"]
    assert asst[0]["type"] == "tool_use"          # no leading empty text block
    assert all(not (b["type"] == "text" and not b["text"].strip()) for b in asst)


def test_stored_tool_call_id_is_preserved():
    out = _to_anthropic_messages(
        [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "toolu_stored", "function": {"name": "f", "arguments": {}}}
            ]},
            {"role": "tool", "tool_call_id": "toolu_stored", "content": "r"},
        ]
    )
    assert out[1]["content"][0]["id"] == "toolu_stored"
    assert out[2]["content"][0]["tool_use_id"] == "toolu_stored"


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------


def test_chat_returns_text_and_tool_calls():
    resp = _Resp(
        [
            _Block("text", text="hello "),
            _Block("tool_use", name="finalize_ruleset", input={"x": 1}, id="toolu_1"),
        ]
    )
    fake = _FakeClient(resp)
    c = AnthropicLLMClient(client=fake)

    out = c.chat(
        model="claude-opus-4-8",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=TOOL_SCHEMAS,
    )

    assert out.text == "hello"
    assert out.tool_calls == [ToolCall(name="finalize_ruleset", arguments={"x": 1}, id="toolu_1")]
    # tools were translated to Anthropic shape and passed through
    assert "function" not in fake.messages.last_kwargs["tools"][0]
    assert fake.messages.last_kwargs["model"] == "claude-opus-4-8"


def test_chat_json_mode_reinforces_system_and_omits_tools():
    fake = _FakeClient(_Resp([_Block("text", text='{"a": 1}')]))
    c = AnthropicLLMClient(client=fake)
    c.chat(model="m", system="base", messages=[{"role": "user", "content": "x"}], format="json")

    assert "JSON" in fake.messages.last_kwargs["system"]
    assert "base" in fake.messages.last_kwargs["system"]
    assert "tools" not in fake.messages.last_kwargs


def test_chat_ignores_unknown_block_types():
    resp = _Resp([_Block("thinking", text="scratch"), _Block("text", text="answer")])
    c = AnthropicLLMClient(client=_FakeClient(resp))
    out = c.chat(model="m", system="s", messages=[{"role": "user", "content": "x"}])
    assert out.text == "answer"
    assert out.tool_calls == []


def test_complete_json_over_fake_client():
    fake = _FakeClient(_Resp([_Block("text", text='Here you go: {"fit": 0.8}')]))
    c = AnthropicLLMClient(client=fake)
    data = complete_json(c, "m", "sys", "classify this")
    assert data == {"fit": 0.8}
    assert fake.messages.last_kwargs["messages"][-1]["role"] == "user"


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        AnthropicLLMClient(api_key="")


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------


def test_get_llm_client_routes_anthropic():
    settings = Settings(llm_provider="anthropic", anthropic_api_key="sk-test")
    assert isinstance(get_llm_client(settings), AnthropicLLMClient)


def test_get_llm_client_routes_ollama():
    settings = Settings(llm_provider="ollama")
    assert isinstance(get_llm_client(settings), OllamaLLMClient)
