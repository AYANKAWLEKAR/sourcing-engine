"""RAG retrieval unit tests with a fake store + deterministic embedder (plan §8.2)."""
from __future__ import annotations

import copy

from sourcing.rag.retriever import (
    SPINE_SOURCES,
    TEXT_SOURCES,
    SourceRetriever,
    build_query_text,
    filter_field_coverage,
    required_fields,
)
from sourcing.rag.vector_store import VectorHit

QUERY = "B2B testing inspection and certification services in QLD Queensland"


def _retriever(fake_vector_store, fake_embedder, seed_registry):
    r = SourceRetriever(fake_vector_store, fake_embedder)
    r.index(seed_registry)
    return r


def test_retriever_indexes_registry(fake_vector_store, fake_embedder, seed_registry):
    _retriever(fake_vector_store, fake_embedder, seed_registry)
    assert len(fake_vector_store) == len(seed_registry)


def test_retrieve_relevant_for_query(fresh_ruleset, fake_vector_store, fake_embedder, seed_registry):
    r = _retriever(fake_vector_store, fake_embedder, seed_registry)
    fresh_ruleset.thesis_summary = QUERY
    plan = r.retrieve(fresh_ruleset, k=8)
    ids = [p.source_id for p in plan]

    # Category/text sources rank in the plan; retail-only source does not.
    assert "google_maps" in ids
    assert "retail_pos_directory" not in ids
    # google_maps should rank above the retail directory by score even if both appeared.
    by_score = {p.source_id: p.score for p in plan}
    assert by_score["google_maps"] > by_score.get("retail_pos_directory", -1)


def test_retrieve_always_includes_spine(fresh_ruleset, fake_vector_store, fake_embedder, seed_registry):
    r = _retriever(fake_vector_store, fake_embedder, seed_registry)
    fresh_ruleset.thesis_summary = QUERY
    for k in (1, 2, 5, 8):
        ids = {p.source_id for p in r.retrieve(fresh_ruleset, k=k)}
        assert ids & SPINE_SOURCES, f"spine missing at k={k}"


def test_retrieve_always_includes_text_source(fresh_ruleset, fake_vector_store, fake_embedder, seed_registry):
    r = _retriever(fake_vector_store, fake_embedder, seed_registry)
    fresh_ruleset.thesis_summary = QUERY
    for k in (2, 5, 8):
        ids = {p.source_id for p in r.retrieve(fresh_ruleset, k=k)}
        assert ids & TEXT_SOURCES, f"text source missing at k={k}"


def test_retrieve_excludes_disabled(fresh_ruleset, fake_vector_store, fake_embedder, seed_registry):
    r = _retriever(fake_vector_store, fake_embedder, seed_registry)
    fresh_ruleset.thesis_summary = QUERY
    ids = {p.source_id for p in r.retrieve(fresh_ruleset, k=15)}
    assert "linkedin_headcount" not in ids  # disabled in the seed


def test_retrieve_field_coverage_filter():
    required = {"country", "state"}
    hits = [
        VectorHit(id="covers", score=0.9, meta={"fields_provided": ["country", "x"]}),
        VectorHit(id="no_overlap", score=0.95, meta={"fields_provided": ["ip", "z"]}),
    ]
    kept = {h.id for h in filter_field_coverage(hits, required)}
    assert kept == {"covers"}


def test_required_fields_are_discovery_relevant(base_ruleset):
    req = required_fields(base_ruleset)
    # SCORE/GATE/EXCLUDE/PROXY_GATE fields present; DEFER fields absent.
    assert {"country", "state", "ebitda_aud", "pe_vc_backed"} <= req
    assert "seller_motivation" not in req
    assert "gross_margin_pct" not in req


def test_retrieve_respects_cost_ceiling(fresh_ruleset, fake_vector_store, fake_embedder, seed_registry):
    from sourcing.models.source import CostTier

    r = _retriever(fake_vector_store, fake_embedder, seed_registry)
    fresh_ruleset.thesis_summary = QUERY
    plan = r.retrieve(fresh_ruleset, k=12, max_cost_tier=CostTier.FREE)
    ids = {p.source_id for p in plan}
    # Paid sources are dropped under a free-only budget...
    assert "crunchbase" not in ids
    assert "ibisworld" not in ids
    assert all(p.cost_tier == CostTier.FREE for p in plan)
    # ...but the invariants still hold (free spine + free website_fetch available).
    assert ids & SPINE_SOURCES
    assert ids & TEXT_SOURCES


def test_filter_field_coverage_no_required_keeps_all():
    hits = [VectorHit(id="a", score=0.1, meta={"fields_provided": ["x"]})]
    assert filter_field_coverage(hits, set()) == hits


def test_build_query_text_falls_back_to_name():
    from sourcing.models.filter_rule import FilterRuleset

    rs = FilterRuleset(ruleset_id="r", name="Fallback Name", base_version="v")
    assert build_query_text(rs) == "Fallback Name"


def test_build_query_text_includes_sector_and_geo(base_ruleset):
    rs = copy.deepcopy(base_ruleset)
    rs.thesis_summary = "founder-owned firms"
    q = build_query_text(rs)
    assert "founder-owned" in q
    assert "testing" in q  # from sector_keyword_match include defaults
    assert "QLD" in q  # from state defaults
