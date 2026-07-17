"""Integration test: FastAPI surface backed by PostgresRunStore.

Proves API ↔ DB without Ollama/Apify: scripted agent + fake pipeline, real store.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.api import create_app
from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.manager import InlineExecutor, RunManager
from sourcing.runs.store import PostgresRunStore
from tests.helpers import scripted_llm, tool_response

pytestmark = pytest.mark.integration

_ABN = f"{uuid.uuid4().int % 10**11:011d}"

_CONFIRM = tool_response(
    ("resolve_sector", {"intent_text": "testing and certification services"}),
    ("resolve_geography", {"states": ["QLD"]}),
    ("finalize_ruleset", {}),
    text="Resolved and finalised.",
)


class FakePipeline:
    def __init__(self, store):
        self._store = store

    def execute(self, run_id, ruleset, *, cache_key=None):
        record = CompanyRecord(
            entity_id=f"abn:{_ABN}", abn=_ABN, legal_name="PG API Co",
            location=Location(state="QLD"),
            provenance=[Provenance(field="abn", source="abn_lookup_api", confidence=0.9)],
        )
        for st in (RunStatus.PLANNING, RunStatus.ACQUIRING, RunStatus.RESOLVING,
                   RunStatus.ENRICHING, RunStatus.RANKING):
            self._store.set_status(run_id, st)
        self._store.save_company(run_id, record)
        self._store.save_shortlist(
            run_id, [RankedCompany(record=record, s_stat=70.0, s_final=0.7)]
        )
        self._store.set_status(run_id, RunStatus.COMPLETE)
        return []


@pytest.fixture
def client(migrated_db):
    store = PostgresRunStore()
    base = load_origo_ruleset()

    def agent_factory():
        return BuyBoxAgent(llm=scripted_llm(_CONFIRM), base_ruleset=base.model_copy(deep=True),
                           model="test-model", max_questions=3)

    manager = RunManager(store, pipeline=FakePipeline(store),
                         agent_factory=agent_factory, executor=InlineExecutor())
    yield TestClient(create_app(manager))

    # Cleanup all test runs created by this module.
    from sourcing.db import session_scope

    with session_scope() as session:
        rows = session.execute(
            text("SELECT run_id FROM runs WHERE run_id LIKE 'run_%'")
        ).fetchall()
        for (rid,) in rows:
            rs = session.execute(
                text("SELECT ruleset_id FROM runs WHERE run_id = :r"), {"r": rid}
            ).scalar_one_or_none()
            session.execute(text("DELETE FROM runs WHERE run_id = :r"), {"r": rid})
            if rs:
                session.execute(text("DELETE FROM rulesets WHERE ruleset_id = :rs"), {"rs": rs})


def test_full_run_persists_through_api(client):
    resp = client.post("/runs", json={"message": "Testing firms in QLD, finalize"})
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    assert resp.json()["ruleset_confirmed"] is True

    status = client.get(f"/runs/{run_id}").json()
    assert status["status"] == "complete"
    assert status["ruleset_id"] == f"rs_{run_id}"
    assert status["shortlist"][0]["record"]["legal_name"] == "PG API Co"
    assert [h["status"] for h in status["stage_history"]][:2] == ["buybox", "planning"]

    detail = client.get(f"/runs/{run_id}/companies/{_ABN}").json()
    assert detail["record"]["legal_name"] == "PG API Co"

    sources = client.get(f"/runs/{run_id}/companies/{_ABN}/sources").json()
    assert sources["provenance"][0]["source"] == "abn_lookup_api"

    select = client.post(f"/runs/{run_id}/select", json={"abn": _ABN})
    assert select.json()["selected"] is True
