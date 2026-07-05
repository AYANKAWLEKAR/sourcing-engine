"""Unit tests for RunPipeline (Part C §4.2) — all components faked, offline.

Asserts the exact §4.1 status-write order, the enrichment checkpoint persistence,
the BuyBox wiring into the orchestrator, and per-stage failure → FAILED with the
stage named in the error.
"""
from __future__ import annotations

import pytest

from sourcing.models.company import CompanyRecord, Location
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.models.source import SourcePlanItem
from sourcing.rank.buybox import BuyBox
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.pipeline import PipelineComponents, RunPipeline
from sourcing.runs.store import InMemoryRunStore


class _Settings:
    run_plan_k = 4
    run_max_places = 5
    run_enrich_workers = 1
    run_top_k = 3
    run_judge_k = 5
    shortlist_gate_n = 3


def _rec(abn: str | None, name: str) -> CompanyRecord:
    return CompanyRecord(
        entity_id=f"x:{name}", abn=abn, legal_name=name, location=Location(state="QLD")
    )


class FakeRetriever:
    def retrieve(self, ruleset, k):
        return [SourcePlanItem(source_id="google_maps", connector_type="scrape",
                               score=0.9, rationale="fake", fields_contributed=[],
                               cost_tier="metered", invariant_tags=[])]


class FakeOrchestrator:
    def __init__(self, pool):
        self._pool = pool
        self.seen_buybox = None

    def fetch_all(self, plan, buybox, *, max_places):
        self.seen_buybox = buybox
        return list(self._pool)


class FakeResolver:
    """Resolves any ABN-less record to a fixed ABN pattern."""

    def __init__(self):
        self.closed = False
        self._n = 0
        self.asic = self  # pipeline calls resolver.asic.close()

    def enrich(self, rec):
        self._n += 1
        rec.abn = f"{self._n:011d}"
        return rec

    def close(self):
        self.closed = True


class FakeEnrichment:
    def enrich_pool(self, pool, buybox, *, max_workers, checkpoint=None):
        for rec in pool:
            rec.business_model = "B2B"
            if checkpoint is not None:
                checkpoint(rec)
        return pool


def fake_ranker(pool, buybox, *, top_k, judge_k):
    ranked = [
        RankedCompany(record=r, s_stat=50.0, s_final=0.5, judge_fit=0.5)
        for r in pool[:top_k]
    ]
    return ranked


class FakeGate:
    def __init__(self):
        self.applied = False

    def apply(self, shortlist):
        self.applied = True
        return shortlist


def _components(pool):
    return PipelineComponents(
        registry_entries=[],
        retriever=FakeRetriever(),
        orchestrator=FakeOrchestrator(pool),
        resolver=FakeResolver(),
        enrichment=FakeEnrichment(),
        ranker=fake_ranker,
        shortlist_gate=FakeGate(),
    )


def _run_pipeline(pool, *, listener=None):
    store = InMemoryRunStore()
    store.create_run("run_t")
    comp = _components(pool)
    pipeline = RunPipeline(store, components=comp, settings=_Settings(), status_listener=listener)
    ruleset = load_origo_ruleset()
    shortlist = pipeline.execute("run_t", ruleset)
    return store, comp, shortlist


def test_status_writes_in_exact_order():
    seen: list[str] = []
    pool = [_rec(None, "A"), _rec(None, "B")]
    store, _, _ = _run_pipeline(pool, listener=lambda rid, st: seen.append(st.value))

    run = store.get_run("run_t")
    assert [h["status"] for h in run.stage_history] == [
        "buybox", "planning", "acquiring", "resolving", "enriching", "ranking", "complete",
    ]
    assert seen == ["planning", "acquiring", "resolving", "enriching", "ranking", "complete"]
    assert run.status == RunStatus.COMPLETE
    assert run.error is None


def test_source_plan_and_coverage_persisted():
    pool = [_rec(None, "A"), _rec(None, "B"), _rec(None, "B")]  # B duplicated pre-resolution
    store, _, _ = _run_pipeline(pool)
    run = store.get_run("run_t")
    assert [p.source_id for p in run.source_plan] == ["google_maps"]
    assert run.coverage["n_raw"] == 3
    assert run.coverage["n_pool"] == 2      # (name, postcode) dedup dropped the copy
    assert run.coverage["n_resolved"] == 2
    assert run.coverage["n_shortlist"] == 2


def test_enrichment_checkpoint_persists_each_company():
    pool = [_rec(None, "A"), _rec(None, "B")]
    store, _, shortlist = _run_pipeline(pool)
    for rc in shortlist:
        found = store.get_company("run_t", rc.record.abn)
        assert found is not None
        company, _ = found
        assert company.business_model == "B2B"  # the enriched state was checkpointed


def test_buybox_from_ruleset_reaches_orchestrator():
    pool = [_rec(None, "A")]
    _, comp, _ = _run_pipeline(pool)
    assert isinstance(comp.orchestrator.seen_buybox, BuyBox)
    assert comp.orchestrator.seen_buybox.thesis  # derived from the ruleset


def test_shortlist_gate_applied_and_resolver_closed():
    pool = [_rec(None, "A")]
    store, comp, _ = _run_pipeline(pool)
    assert comp.shortlist_gate.applied is True
    assert comp.resolver.closed is True
    assert store.get_run("run_t").shortlist  # persisted


@pytest.mark.parametrize(
    ("break_component", "expect_stage"),
    [
        ("retriever", "planning"),
        ("orchestrator", "acquiring"),
        ("resolver", "resolving"),
        ("enrichment", "enriching"),
        ("ranker", "ranking"),
    ],
)
def test_failure_at_each_stage_marks_failed(break_component, expect_stage):
    pool = [_rec(None, "A")]
    comp = _components(pool)

    def boom(*a, **k):
        raise RuntimeError("boom")

    if break_component == "retriever":
        comp.retriever.retrieve = boom
    elif break_component == "orchestrator":
        comp.orchestrator.fetch_all = boom
    elif break_component == "resolver":
        comp.resolver.enrich = boom
    elif break_component == "enrichment":
        comp.enrichment.enrich_pool = boom
    elif break_component == "ranker":
        comp.ranker = boom

    store = InMemoryRunStore()
    store.create_run("run_t")
    pipeline = RunPipeline(store, components=comp, settings=_Settings())
    with pytest.raises(RuntimeError):
        pipeline.execute("run_t", load_origo_ruleset())

    run = store.get_run("run_t")
    assert run.status == RunStatus.FAILED
    assert run.error.startswith(f"{expect_stage}:")
