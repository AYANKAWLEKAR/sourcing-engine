"""TestClient tests for the FastAPI run surface (Part C §4.3) — fully offline.

The manager is real; the agent is scripted, the pipeline is a fake that walks the
§4.1 stages and persists a shortlist, the store is in-memory, and the executor is
inline — so a POST /runs that confirms immediately returns with the run already
complete and pollable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.api import create_app
from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.manager import InlineExecutor, RunManager
from sourcing.runs.store import InMemoryRunStore
from tests.helpers import scripted_llm, tool_response

MODEL = "test-model"
_ABN = "1" * 11

_RESOLVE_AND_FINALIZE = tool_response(
    ("resolve_sector", {"intent_text": "testing and certification services"}),
    ("resolve_geography", {"states": ["QLD"]}),
    ("finalize_ruleset", {}),
    text="Resolved and finalised.",
)


class FakePipeline:
    """Walks the real stage sequence and persists one company + shortlist."""

    def __init__(self, store):
        self._store = store

    def execute(self, run_id, ruleset, *, cache_key=None):
        record = CompanyRecord(
            entity_id=f"abn:{_ABN}", abn=_ABN, legal_name="Acme Air",
            location=Location(state="QLD", postcode="4000"),
            provenance=[Provenance(field="abn", source="abn_lookup_api", confidence=0.9)],
        )
        for st in (RunStatus.PLANNING, RunStatus.ACQUIRING, RunStatus.RESOLVING,
                   RunStatus.ENRICHING, RunStatus.RANKING):
            self._store.set_status(run_id, st)
        self._store.update_coverage(run_id, n_raw=5, n_resolved=4, n_shortlist=1)
        self._store.save_company(run_id, record)
        self._store.save_shortlist(
            run_id,
            [RankedCompany(record=record, s_stat=70.0, s_final=0.7, judge_fit=0.7,
                           judge_rationale="fits", standout_signals=["award finalist"])],
        )
        self._store.set_status(run_id, RunStatus.COMPLETE)
        return []


@pytest.fixture
def client():
    store = InMemoryRunStore()
    base = load_origo_ruleset()

    scripts = {"queue": []}

    def agent_factory():
        responses = scripts["queue"] or [_RESOLVE_AND_FINALIZE]
        return BuyBoxAgent(llm=scripted_llm(*responses), base_ruleset=base.model_copy(deep=True),
                           model=MODEL, max_questions=3)

    manager = RunManager(store, pipeline=FakePipeline(store),
                         agent_factory=agent_factory, executor=InlineExecutor())
    test_client = TestClient(create_app(manager))
    test_client.scripts = scripts  # let tests swap the agent script
    return test_client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_post_runs_confirm_and_poll_to_complete(client):
    resp = client.post("/runs", json={"message": "Testing firms in QLD, defaults, finalize"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["ruleset_confirmed"] is True
    run_id = body["run_id"]

    status = client.get(f"/runs/{run_id}").json()
    assert status["status"] == "complete"
    assert [h["status"] for h in status["stage_history"]] == [
        "buybox", "planning", "acquiring", "resolving", "enriching", "ranking", "complete",
    ]
    assert status["coverage"]["n_shortlist"] == 1
    assert status["ruleset_id"] == f"rs_{run_id}"
    assert len(status["shortlist"]) == 1
    assert status["shortlist"][0]["record"]["legal_name"] == "Acme Air"


def test_multi_turn_buybox_over_api(client):
    client.scripts["queue"] = [
        LLMResponse(text="Which states should I target?"),
        _RESOLVE_AND_FINALIZE,
    ]
    resp = client.post("/runs", json={"message": "Founder-owned testing firms"})
    body = resp.json()
    assert body["ruleset_confirmed"] is False
    assert "?" in body["reply"]
    run_id = body["run_id"]

    resp2 = client.post(f"/runs/{run_id}/buybox", json={"message": "QLD only, finalize"})
    body2 = resp2.json()
    assert body2["ruleset_confirmed"] is True
    assert client.get(f"/runs/{run_id}").json()["status"] == "complete"


def test_company_detail_and_sources(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]

    detail = client.get(f"/runs/{run_id}/companies/{_ABN}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["record"]["legal_name"] == "Acme Air"
    assert body["selected"] is False

    sources = client.get(f"/runs/{run_id}/companies/{_ABN}/sources").json()
    assert sources["provenance"][0]["field"] == "abn"
    assert sources["provenance"][0]["confidence"] == 0.9


def test_select_company(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]
    resp = client.post(f"/runs/{run_id}/select", json={"abn": _ABN})
    assert resp.status_code == 200
    assert resp.json()["selected"] is True
    assert client.get(f"/runs/{run_id}/companies/{_ABN}").json()["selected"] is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_get_unknown_run_404(client):
    assert client.get("/runs/run_nope").status_code == 404


def test_buybox_unknown_run_404(client):
    assert client.post("/runs/run_nope/buybox", json={"message": "x"}).status_code == 404


def test_buybox_past_stage_409(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]
    resp = client.post(f"/runs/{run_id}/buybox", json={"message": "more"})
    assert resp.status_code == 409
    assert "past the buybox stage" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Saved chats, listing, labelling, conversational re-rank, selected list
# ---------------------------------------------------------------------------

def test_conversation_persisted_and_returned(client):
    run_id = client.post("/runs", json={"message": "Testing firms in QLD, finalize"}).json()["run_id"]
    convo = client.get(f"/runs/{run_id}").json()["conversation"]
    assert convo[0]["role"] == "user"
    assert convo[0]["text"] == "Testing firms in QLD, finalize"


def test_list_runs_endpoint(client):
    id1 = client.post("/runs", json={"message": "buy box one"}).json()["run_id"]
    id2 = client.post("/runs", json={"message": "buy box two"}).json()["run_id"]
    runs = client.get("/runs").json()["runs"]
    ids = {r["run_id"] for r in runs}
    assert {id1, id2} <= ids
    assert any(r["thesis"] == "buy box two" for r in runs)


def test_label_run_endpoint(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]
    resp = client.patch(f"/runs/{run_id}", json={"label": "My saved search"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "My saved search"
    assert client.patch("/runs/run_nope", json={"label": "x"}).status_code == 404


def test_selected_list_endpoint(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]
    client.post(f"/runs/{run_id}/select", json={"abn": _ABN})
    body = client.get(f"/runs/{run_id}/selected").json()
    assert [c["legal_name"] for c in body["companies"]] == ["Acme Air"]


def test_query_endpoint_reranks(client, monkeypatch):
    from sourcing.rank import pool_query

    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]

    def _fake_parse(text, thesis="", **kw):
        return pool_query.QuerySpec(
            filters=[pool_query.Filter(field="state", op="eq", value="QLD")],
            sort_by="s_final", order="desc")

    monkeypatch.setattr(pool_query, "parse_query", _fake_parse)
    resp = client.post(f"/runs/{run_id}/query", json={"message": "QLD only"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["record"]["legal_name"] == "Acme Air"
    assert body["spec"]["sort_by"] == "s_final"


def test_query_endpoint_409_without_shortlist(client):
    # A run parked in buybox (multi-turn) has no shortlist yet.
    client.scripts["queue"] = [LLMResponse(text="Which states?")]
    run_id = client.post("/runs", json={"message": "vague"}).json()["run_id"]
    resp = client.post(f"/runs/{run_id}/query", json={"message": "gov contracts only"})
    assert resp.status_code == 409


def test_company_not_in_run_404(client):
    run_id = client.post("/runs", json={"message": "confirm"}).json()["run_id"]
    assert client.get(f"/runs/{run_id}/companies/99999999999").status_code == 404
    assert client.post(f"/runs/{run_id}/select", json={"abn": "9" * 11}).status_code == 404


def test_empty_message_422(client):
    assert client.post("/runs", json={"message": ""}).status_code == 422
