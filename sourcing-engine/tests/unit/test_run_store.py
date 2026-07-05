"""Unit tests for the InMemoryRunStore (Part C §4.1) — offline."""
from __future__ import annotations

from sourcing.models.company import CompanyRecord, Location
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.runs.store import InMemoryRunStore


def _store_with_run(run_id: str = "run_1") -> InMemoryRunStore:
    store = InMemoryRunStore()
    store.create_run(run_id)
    return store


def _record(abn: str = "1" * 11) -> CompanyRecord:
    return CompanyRecord(
        entity_id=f"abn:{abn}", abn=abn, legal_name="Acme Air",
        location=Location(state="QLD"), website_text_raw="x" * 50,
    )


def test_create_and_get_run_snapshot():
    store = _store_with_run()
    run = store.get_run("run_1")
    assert run is not None
    assert run.status == RunStatus.BUYBOX
    assert run.stage_history[0]["status"] == "buybox"
    assert run.created_at and run.updated_at


def test_get_unknown_run_returns_none():
    assert InMemoryRunStore().get_run("nope") is None


def test_set_status_appends_history_in_order():
    store = _store_with_run()
    for st in (RunStatus.PLANNING, RunStatus.ACQUIRING, RunStatus.COMPLETE):
        store.set_status("run_1", st)
    run = store.get_run("run_1")
    assert run.status == RunStatus.COMPLETE
    assert [h["status"] for h in run.stage_history] == [
        "buybox", "planning", "acquiring", "complete",
    ]


def test_set_status_failed_records_error():
    store = _store_with_run()
    store.set_status("run_1", RunStatus.FAILED, error="acquiring: boom")
    run = store.get_run("run_1")
    assert run.status == RunStatus.FAILED
    assert run.error == "acquiring: boom"


def test_save_and_get_company_with_selected_flag():
    store = _store_with_run()
    rec = _record()
    store.save_company("run_1", rec)

    found = store.get_company("run_1", rec.abn)
    assert found is not None
    company, selected = found
    assert company.legal_name == "Acme Air"
    assert selected is False

    assert store.mark_selected("run_1", rec.abn) is True
    _, selected = store.get_company("run_1", rec.abn)
    assert selected is True


def test_mark_selected_unknown_company_returns_false():
    store = _store_with_run()
    assert store.mark_selected("run_1", "9" * 11) is False


def test_save_company_ignores_abnless_records():
    store = _store_with_run()
    store.save_company("run_1", CompanyRecord(entity_id="maps:x", legal_name="No ABN"))
    assert store.get_company("run_1", "") is None


def test_save_company_preserves_selected_on_recheckpoint():
    store = _store_with_run()
    rec = _record()
    store.save_company("run_1", rec)
    store.mark_selected("run_1", rec.abn)
    store.save_company("run_1", rec)  # shortlist-gate re-checkpoint
    _, selected = store.get_company("run_1", rec.abn)
    assert selected is True


def test_shortlist_roundtrip_excludes_website_text():
    store = _store_with_run()
    rc = RankedCompany(record=_record(), s_stat=88.0, s_final=0.71,
                       judge_fit=0.6, judge_rationale="fits", standout_signals=["award finalist"])
    store.save_shortlist("run_1", [rc])
    run = store.get_run("run_1")
    assert run.shortlist is not None and len(run.shortlist) == 1
    dumped = run.shortlist[0]
    assert dumped["s_final"] == 0.71
    assert dumped["record"]["legal_name"] == "Acme Air"
    # The bulky raw page text is excluded from the shortlist dump.
    assert "website_text_raw" not in dumped["record"]


def test_coverage_merges_counters():
    store = _store_with_run()
    store.update_coverage("run_1", n_raw=50)
    store.update_coverage("run_1", n_resolved=40)
    assert store.get_run("run_1").coverage == {"n_raw": 50, "n_resolved": 40}


def test_ruleset_and_attach():
    from sourcing.ruleset.loader import load_origo_ruleset

    store = _store_with_run()
    rs = load_origo_ruleset()
    rs.ruleset_id = "rs_run_1"
    store.save_ruleset(rs)
    store.attach_ruleset("run_1", "rs_run_1")
    assert store.get_run("run_1").ruleset_id == "rs_run_1"
