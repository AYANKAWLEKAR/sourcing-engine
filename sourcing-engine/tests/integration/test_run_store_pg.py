"""Integration tests for PostgresRunStore — needs Postgres (migration 0002 applied).

Uses the existing migrated_db fixture (alembic upgrade head). No Ollama/Apify.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.models.source import SourcePlanItem
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.store import PostgresRunStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store(migrated_db):
    return PostgresRunStore()


@pytest.fixture
def run_id(store):
    rid = f"run_test_{uuid.uuid4().hex[:8]}"
    store.create_run(rid)
    yield rid
    # Cleanup (cascades to run_companies).
    from sourcing.db import session_scope

    with session_scope() as session:
        session.execute(text("DELETE FROM runs WHERE run_id = :r"), {"r": rid})
        session.execute(
            text("DELETE FROM rulesets WHERE ruleset_id = :rs"), {"rs": f"rs_{rid}"}
        )


def _record(abn: str) -> CompanyRecord:
    return CompanyRecord(
        entity_id=f"abn:{abn}", abn=abn, legal_name="PG Test Co",
        location=Location(state="QLD", postcode="4000"),
        provenance=[Provenance(field="abn", source="abn_lookup_api", confidence=0.9)],
        website_text_raw="some page text",
    )


def test_run_lifecycle_status_and_history(store, run_id):
    run = store.get_run(run_id)
    assert run.status == RunStatus.BUYBOX

    store.set_status(run_id, RunStatus.PLANNING)
    store.set_status(run_id, RunStatus.FAILED, error="planning: boom")

    run = store.get_run(run_id)
    assert run.status == RunStatus.FAILED
    assert run.error == "planning: boom"
    assert [h["status"] for h in run.stage_history] == ["buybox", "planning", "failed"]
    assert run.created_at and run.updated_at


def test_ruleset_persisted_with_rules(store, run_id):
    rs = load_origo_ruleset()
    rs.ruleset_id = f"rs_{run_id}"
    rs.confirmed = True
    store.save_ruleset(rs)
    store.attach_ruleset(run_id, rs.ruleset_id)

    from sourcing.db import session_scope

    with session_scope() as session:
        n_rules = session.execute(
            text("SELECT count(*) FROM filter_rules WHERE ruleset_id = :rs"),
            {"rs": rs.ruleset_id},
        ).scalar_one()
        confirmed = session.execute(
            text("SELECT confirmed FROM rulesets WHERE ruleset_id = :rs"),
            {"rs": rs.ruleset_id},
        ).scalar_one()
    assert n_rules == len(rs.rules)
    assert confirmed is True
    assert store.get_run(run_id).ruleset_id == rs.ruleset_id

    # Idempotent re-save (delete-then-insert).
    store.save_ruleset(rs)
    with session_scope() as session:
        n_rules_again = session.execute(
            text("SELECT count(*) FROM filter_rules WHERE ruleset_id = :rs"),
            {"rs": rs.ruleset_id},
        ).scalar_one()
    assert n_rules_again == len(rs.rules)


def test_company_roundtrip_and_selection(store, run_id):
    abn = f"{uuid.uuid4().int % 10**11:011d}"
    store.save_company(run_id, _record(abn))

    found = store.get_company(run_id, abn)
    assert found is not None
    company, selected = found
    assert company.legal_name == "PG Test Co"
    assert company.website_text_raw == "some page text"  # full record persisted
    assert selected is False

    assert store.mark_selected(run_id, abn) is True
    _, selected = store.get_company(run_id, abn)
    assert selected is True

    # Re-checkpoint keeps membership unique + selection intact.
    store.save_company(run_id, _record(abn))
    _, selected = store.get_company(run_id, abn)
    assert selected is True


def test_source_plan_coverage_shortlist(store, run_id):
    plan = [SourcePlanItem(source_id="google_maps", connector_type="scrape", score=0.9,
                           rationale="r", fields_contributed=["state"], cost_tier="metered",
                           invariant_tags=["text"])]
    store.save_source_plan(run_id, plan)
    store.update_coverage(run_id, n_raw=10)
    store.update_coverage(run_id, n_resolved=8)

    abn = f"{uuid.uuid4().int % 10**11:011d}"
    rc = RankedCompany(record=_record(abn), s_stat=70.0, s_final=0.7,
                       judge_fit=0.6, standout_signals=["award finalist"])
    store.save_shortlist(run_id, [rc])

    run = store.get_run(run_id)
    assert run.source_plan[0].source_id == "google_maps"
    assert run.coverage == {"n_raw": 10, "n_resolved": 8}
    assert run.shortlist[0]["s_final"] == 0.7
    assert "website_text_raw" not in run.shortlist[0]["record"]  # excluded from dump
