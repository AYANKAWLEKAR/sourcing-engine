"""Buy-Box agent unit tests with a scripted mock LLM (plan §8.2)."""
from __future__ import annotations

import pytest

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.agent.tools import FinalizeError, RulesetEditor
from sourcing.llm import LLMResponse
from tests.helpers import scripted_llm, tool_response

MODEL = "test-model"


# --- tool handler tests (no LLM needed) ---
def test_tool_update_ruleset(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    out = editor.update_ruleset("ebitda_aud", {"min": 2_000_000, "max": 20_000_000})
    assert out["ok"] is True
    assert editor.ruleset.rule("ebitda_aud").logic == {"min": 2_000_000, "max": 20_000_000}


def test_tool_update_ruleset_unknown_field(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    assert editor.update_ruleset("does_not_exist", {})["ok"] is False


def test_tool_resolve_sector(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    out = editor.resolve_sector("testing and certification services")
    assert out["anzsic_codes"]
    assert out["keywords"]
    # written into the ruleset
    assert editor.ruleset.rule("anzsic_code").logic["values"]
    assert editor.ruleset.rule("sector_keyword_match").logic["include"]


def test_tool_resolve_geography(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    out = editor.resolve_geography(states=["QLD"])
    assert out["postcodes"]
    assert "QLD" in out["states"]
    assert editor.ruleset.rule("state").logic["postcodes"]


def test_tool_resolve_geography_region(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    out = editor.resolve_geography(regions=["Brisbane"])
    assert "QLD" in out["states"]


def test_tool_finalize_incomplete_raises(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    with pytest.raises(FinalizeError):
        editor.finalize_ruleset()


def test_tool_finalize_complete_confirms(fresh_ruleset):
    editor = RulesetEditor(fresh_ruleset)
    editor.resolve_sector("testing inspection certification")
    editor.resolve_geography(states=["QLD", "NSW"])
    out = editor.finalize_ruleset()
    assert out["confirmed"] is True
    assert editor.ruleset.confirmed is True
    assert editor.ruleset.thesis_summary


# --- agent loop tests ---
def test_agent_conversation_reaches_confirmed(fresh_ruleset):
    llm = scripted_llm(
        tool_response(
            ("resolve_sector", {"intent_text": "testing and certification services"}),
            ("resolve_geography", {"states": ["QLD"]}),
            text="Resolving sector and geography.",
        ),
        tool_response(("finalize_ruleset", {}), text="Finalising."),
    )
    agent = BuyBoxAgent(llm=llm, base_ruleset=fresh_ruleset, model=MODEL, max_questions=6)

    turn1 = agent.step("Founder-owned testing & certification firms in QLD")
    assert not turn1.done
    assert agent.state.rule("anzsic_code").logic["values"]

    turn2 = agent.step("Looks good, finalize.")
    assert turn2.done
    assert turn2.ruleset.confirmed is True
    assert not turn2.needs_review
    assert turn2.ruleset.thesis_summary


def test_agent_respects_question_cap(fresh_ruleset):
    llm = scripted_llm(
        LLMResponse(text="Which states should I target?"),
        LLMResponse(text="Tighten the EBITDA floor above $1M?"),
    )
    agent = BuyBoxAgent(llm=llm, base_ruleset=fresh_ruleset, model=MODEL, max_questions=2)

    turn1 = agent.step("I want to buy a business")
    assert not turn1.done
    assert agent.questions_asked == 1

    turn2 = agent.step("NSW and QLD")
    assert turn2.done
    assert turn2.needs_review is True
    assert turn2.ruleset.confirmed is False
