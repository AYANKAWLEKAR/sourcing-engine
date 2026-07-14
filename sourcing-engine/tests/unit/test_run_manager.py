"""Unit tests for RunManager (Part C) — scripted agent, fake pipeline, inline executor."""
from __future__ import annotations

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.llm import LLMResponse
from sourcing.models.run import RunStatus
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.manager import InlineExecutor, RunManager
from sourcing.runs.store import InMemoryRunStore
from tests.helpers import scripted_llm, tool_response

MODEL = "test-model"

# Scripts mirror tests/unit/test_agent.py's known-good tool sequences.
_RESOLVE = tool_response(
    ("resolve_sector", {"intent_text": "testing and certification services"}),
    ("resolve_geography", {"states": ["QLD"]}),
    text="Resolving sector and geography.",
)
_FINALIZE = tool_response(("finalize_ruleset", {}), text="Finalising.")
_RESOLVE_AND_FINALIZE = tool_response(
    ("resolve_sector", {"intent_text": "testing and certification services"}),
    ("resolve_geography", {"states": ["QLD"]}),
    ("finalize_ruleset", {}),
    text="Resolved and finalised.",
)


class FakePipeline:
    def __init__(self, store):
        self._store = store
        self.executed: list[str] = []
        self.rulesets: list = []
        self.cache_keys: list = []

    def execute(self, run_id, ruleset, *, cache_key=None):
        self.executed.append(run_id)
        self.rulesets.append(ruleset)
        self.cache_keys.append(cache_key)
        self._store.set_status(run_id, RunStatus.COMPLETE)
        return []


def _manager(*llm_responses):
    store = InMemoryRunStore()
    pipeline = FakePipeline(store)
    base = load_origo_ruleset()

    def agent_factory():
        return BuyBoxAgent(
            llm=scripted_llm(*llm_responses), base_ruleset=base.model_copy(deep=True),
            model=MODEL, max_questions=3,
        )

    manager = RunManager(
        store, pipeline=pipeline, agent_factory=agent_factory, executor=InlineExecutor(),
    )
    return manager, store, pipeline


def test_confirm_on_first_turn_launches_pipeline():
    manager, store, pipeline = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("Testing & certification firms in QLD, finalize with defaults")

    assert result.turn.ruleset.confirmed is True
    assert pipeline.executed == [result.run_id]
    assert not manager.has_session(result.run_id)          # session not parked
    run = store.get_run(result.run_id)
    assert run.status == RunStatus.COMPLETE
    assert run.ruleset_id == f"rs_{result.run_id}"          # per-run PK rewrite


def test_multi_turn_confirm_launches_after_answer():
    manager, store, pipeline = _manager(
        LLMResponse(text="Which states should I target?"),   # clarifying question
        _RESOLVE_AND_FINALIZE,
    )
    result = manager.start_run("Founder-owned testing firms")
    assert not result.turn.ruleset.confirmed
    assert manager.has_session(result.run_id)                # parked, awaiting answer
    assert store.get_run(result.run_id).status == RunStatus.BUYBOX

    turn2 = manager.continue_buybox(result.run_id, "QLD only, finalize")
    assert turn2.ruleset.confirmed is True
    assert pipeline.executed == [result.run_id]
    assert not manager.has_session(result.run_id)            # session popped


def test_question_cap_stays_in_buybox_with_needs_review():
    manager, store, pipeline = _manager(
        LLMResponse(text="Which states?"),
        LLMResponse(text="What EBITDA range?"),
        LLMResponse(text="Which sectors exactly?"),
    )
    result = manager.start_run("I want to buy a business")
    turn = manager.continue_buybox(result.run_id, "not sure")
    turn = manager.continue_buybox(result.run_id, "still not sure")

    assert turn.needs_review is True
    assert not turn.ruleset.confirmed
    assert pipeline.executed == []                            # never launched
    assert store.get_run(result.run_id).status == RunStatus.BUYBOX


def test_continue_unknown_run_raises_keyerror():
    manager, _, _ = _manager()
    try:
        manager.continue_buybox("run_nope", "hello")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_continue_past_buybox_raises_lookuperror():
    manager, store, pipeline = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("confirm immediately")
    try:
        manager.continue_buybox(result.run_id, "more input")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "past the buybox stage" in str(exc)


def test_ruleset_persisted_with_rewritten_id():
    manager, store, pipeline = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("confirm immediately")
    saved = store._rulesets[f"rs_{result.run_id}"]           # InMemory internals
    assert saved.confirmed is True
    assert pipeline.rulesets[0].ruleset_id == f"rs_{result.run_id}"


# ---------------------------------------------------------------------------
# Saved chats + conversational re-rank
# ---------------------------------------------------------------------------

def test_conversation_is_persisted_each_turn():
    manager, store, _ = _manager(
        LLMResponse(text="Which states should I target?"),
        _RESOLVE_AND_FINALIZE,
    )
    result = manager.start_run("Founder-owned testing firms")
    manager.continue_buybox(result.run_id, "QLD only, finalize")
    convo = store.get_run(result.run_id).conversation
    roles = [m["role"] for m in convo]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert convo[0]["text"] == "Founder-owned testing firms"


def test_list_runs_returns_newest_first_with_thesis():
    manager, _, _ = _manager(_RESOLVE_AND_FINALIZE, _RESOLVE_AND_FINALIZE)
    r1 = manager.start_run("first buy box")
    r2 = manager.start_run("second buy box")
    runs = manager.list_runs()
    ids = [r["run_id"] for r in runs]
    assert set(ids) == {r1.run_id, r2.run_id}
    # thesis comes from the first (user) conversation turn
    assert any(r["thesis"] == "second buy box" for r in runs)


def test_set_label_and_unknown_run():
    manager, store, _ = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("confirm immediately")
    assert manager.set_label(result.run_id, "HVAC — Sydney") is True
    assert store.get_run(result.run_id).label == "HVAC — Sydney"
    assert manager.set_label("run_nope", "x") is False


def test_query_shortlist_reranks_persisted_shortlist():
    from sourcing.models.company import CompanyRecord, Location, MoatSignals
    from sourcing.models.ranking import RankedCompany

    manager, store, _ = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("confirm immediately")

    gov = RankedCompany(
        record=CompanyRecord(entity_id="x:gov", abn="1" * 11, legal_name="GovCo",
                             location=Location(state="QLD"),
                             moat_signals=MoatSignals(gov_contracts=True)),
        s_stat=50.0, s_evidence=0.3, s_final=0.6)
    plain = RankedCompany(
        record=CompanyRecord(entity_id="x:plain", abn="2" * 11, legal_name="PlainCo",
                             location=Location(state="QLD")),
        s_stat=50.0, s_evidence=0.0, s_final=0.8)
    store.save_shortlist(result.run_id, [gov, plain])

    # No shortlist-aware LLM in this offline test → force a deterministic spec.
    from sourcing.rank import pool_query

    def _fake_parse(text, thesis="", **kw):
        return pool_query.QuerySpec(
            filters=[pool_query.Filter(field="gov_contracts", op="is_true")],
            sort_by="s_final", order="desc")

    orig = pool_query.parse_query
    pool_query.parse_query = _fake_parse
    try:
        out = manager.query_shortlist(result.run_id, "only ones with gov contracts")
    finally:
        pool_query.parse_query = orig

    names = [r["record"]["legal_name"] for r in out["results"]]
    assert names == ["GovCo"]
    # The exchange was saved into the conversation.
    convo = store.get_run(result.run_id).conversation
    assert convo[-1]["role"] == "assistant"
    assert "Re-ranked" in convo[-1]["text"]


def test_list_selected_returns_marked_companies():
    from sourcing.models.company import CompanyRecord, Location

    manager, store, _ = _manager(_RESOLVE_AND_FINALIZE)
    result = manager.start_run("confirm immediately")
    rec = CompanyRecord(entity_id="x:a", abn="3" * 11, legal_name="Picked",
                        location=Location(state="QLD"))
    store.save_company(result.run_id, rec)
    assert manager.select(result.run_id, "3" * 11) is True
    picked = manager.list_selected(result.run_id)
    assert [c.legal_name for c in picked] == ["Picked"]
