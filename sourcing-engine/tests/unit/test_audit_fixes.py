"""Unit tests for the 18 audit fixes.

All offline — no network, no Apify, no Ollama, no Postgres.

Fixes covered here:
  1 + 2 + 3  SourcingOrchestrator / params_for_connector
  4           BuyBox.from_ruleset sector_exclude_match.include
  5           EntityResolver.resolve returns 3-tuple (thread-safe)
  6           Cache singleton (shared default cache)
  7           BulkConnector ensure_loaded thread-lock
  8           SignalExtractor text truncation constant
  9           deduplicate_by_abn / deduplicate_pre_resolution
  10          EnrichmentNode uses normalize() contract
  11          JudgeResult.unavailable + RankedCompany.judge_unavailable
  12          AusTender window configurable (730 days default)
  13          AwardRegisterConnector degradation warning
  14          entity_id collision hash-fallback (GoogleMaps + YellowPages)
  15          EnrichmentNode concurrent enrichment (ThreadPoolExecutor)
  16          _RateLimiter thread-safe acquire()
  17          ConnectorRegistry singleton / get_or_create
  18          EnrichmentNode checkpoint callback
"""
from __future__ import annotations

import json
import threading
import time
import warnings
from unittest.mock import MagicMock, patch

import pytest

from sourcing.connectors.cache import InMemoryTTLCache, get_default_cache, reset_default_cache
from sourcing.connectors.connector_registry import ConnectorRegistry
from sourcing.connectors.dedup import deduplicate_by_abn, deduplicate_pre_resolution
from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.enrichment.entity_resolution import EntityResolver
from sourcing.enrichment.signal_extractor import _MAX_TEXT_CHARS, SignalExtractor
from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.rank.buybox import BuyBox
from sourcing.rank.judge import LLMJudge
from sourcing.rank.rank import rank_pool

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _co(name, *, abn=None, state="QLD", pc="4000", provenance_count=0):
    r = CompanyRecord(
        entity_id=f"maps:{name}",
        abn=abn,
        legal_name=name,
        location=Location(state=state, postcode=pc),
    )
    for i in range(provenance_count):
        r.provenance.append(Provenance(field=f"f{i}", source="x", confidence=0.9))
    return r


_BB = BuyBox(
    thesis="HVAC in QLD",
    sector_keywords=["hvac", "air conditioning"],
    sector_exclude_keywords=[],
    states=["QLD"],
    target_models=["B2B"],
    min_years=3,
)


# ===========================================================================
# Fix 1 + 2 + 3 — SourcingOrchestrator / params_for_connector
# ===========================================================================

class TestParamsForConnector:
    from sourcing.orchestrator import params_for_connector

    def test_scrape_tiles_per_state(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["hvac"], states=["QLD", "NSW"])
        tiles = params_for_connector("google_maps", bb)
        assert len(tiles) == 2
        locations = {t["location"] for t in tiles}
        assert any("Queensland" in loc for loc in locations)
        assert any("New South Wales" in loc for loc in locations)

    def test_scrape_keywords_in_tile(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["hvac", "refrigeration"], states=["QLD"])
        tile = params_for_connector("google_maps", bb)[0]
        assert "hvac" in tile["search_terms"]
        assert "refrigeration" in tile["search_terms"]

    def test_spine_no_state_filter(self):
        """Fix 3: ASIC must never receive a state filter from the orchestrator."""
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["hvac"], states=["QLD"])
        params = params_for_connector("asic_company_dataset", bb)
        assert len(params) == 1
        assert "state" not in params[0]

    def test_spine_passes_structural_filters(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(min_years=5, states=["QLD"])
        params = params_for_connector("asic_company_dataset", bb, entity_types=["APTY"])[0]
        assert params["min_years"] == 5
        assert params["entity_types"] == ["APTY"]
        assert "state" not in params

    def test_enrichment_sources_return_empty_params(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox()
        assert params_for_connector("austender", bb) == [{}]
        assert params_for_connector("website_fetch", bb) == [{}]

    def test_single_state_single_tile(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["hvac"], states=["QLD"])
        assert len(params_for_connector("google_maps", bb)) == 1

    def test_no_states_falls_back_to_australia(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["hvac"], states=[])
        tiles = params_for_connector("google_maps", bb)
        assert len(tiles) == 1
        assert "Australia" in tiles[0]["location"]

    def test_yellow_pages_tiles_by_state(self):
        from sourcing.orchestrator import params_for_connector
        bb = BuyBox(sector_keywords=["plumber"], states=["VIC", "SA"])
        tiles = params_for_connector("yellow_pages", bb)
        assert len(tiles) == 2


class TestSourcingOrchestrator:
    def _make_orchestrator(self, records_by_source: dict):
        """Build an orchestrator with fake connectors returning fixed records."""
        from sourcing.models.source import (
            ConnectorType,
            SourcePlanItem,
            SourceRegistryEntry,
        )
        from sourcing.orchestrator import SourcingOrchestrator

        entries = []
        for source_id, _records in records_by_source.items():
            entries.append(SourceRegistryEntry(
                source_id=source_id,
                connector_type=ConnectorType.SCRAPE,
                connector_ref=f"fake.{source_id}",
                enabled=True,
                capability_doc="test",
            ))

        class FakeConnector:
            def __init__(self, recs):
                self._recs = recs
            def fetch(self, params):
                return [{"name": r.legal_name} for r in self._recs]
            def normalize(self, raw):
                name = raw["name"]
                return next(r for r in self._recs if r.legal_name == name)

        fake_registry = MagicMock()
        fake_registry.get_or_create.side_effect = lambda ref, **kw: FakeConnector(
            records_by_source.get(ref.split(".")[-1], [])
        )

        orch = SourcingOrchestrator(entries, connector_registry=fake_registry)
        plan = [
            SourcePlanItem(
                source_id=sid,
                connector_type=ConnectorType.SCRAPE,
                score=0.8,
                rationale="test",
            )
            for sid in records_by_source
        ]
        return orch, plan

    def test_fetch_all_aggregates_records(self):
        records = {"google_maps": [_co("Acme Air"), _co("Cool HVAC")]}
        orch, plan = self._make_orchestrator(records)
        pool = orch.fetch_all(plan, _BB)
        names = {r.legal_name for r in pool}
        assert "Acme Air" in names and "Cool HVAC" in names

    def test_shortlist_gated_source_skipped(self):
        from sourcing.models.source import ConnectorType, SourcePlanItem, SourceRegistryEntry
        from sourcing.orchestrator import SourcingOrchestrator

        entry = SourceRegistryEntry(
            source_id="linkedin_headcount",
            connector_type=ConnectorType.SCRAPE,
            connector_ref="fake.linkedin",
            enabled=True,
            gate="shortlist_only",
            capability_doc="",
        )
        fake_conn = MagicMock()
        fake_registry = MagicMock()
        fake_registry.get_or_create.return_value = fake_conn

        orch = SourcingOrchestrator([entry], connector_registry=fake_registry)
        plan = [SourcePlanItem(
            source_id="linkedin_headcount",
            connector_type=ConnectorType.SCRAPE,
            score=0.5,
            rationale="",
        )]
        pool = orch.fetch_all(plan, _BB)
        assert pool == []
        fake_conn.fetch.assert_not_called()

    def test_connector_error_warns_not_crashes(self):
        from sourcing.models.source import ConnectorType, SourcePlanItem, SourceRegistryEntry
        from sourcing.orchestrator import SourcingOrchestrator

        entry = SourceRegistryEntry(
            source_id="google_maps",
            connector_type=ConnectorType.SCRAPE,
            connector_ref="fake.google_maps",
            enabled=True,
            capability_doc="",
        )
        failing_conn = MagicMock()
        failing_conn.fetch.side_effect = RuntimeError("actor blew up")
        fake_registry = MagicMock()
        fake_registry.get_or_create.return_value = failing_conn

        orch = SourcingOrchestrator([entry], connector_registry=fake_registry)
        plan = [SourcePlanItem(
            source_id="google_maps",
            connector_type=ConnectorType.SCRAPE,
            score=0.7,
            rationale="",
        )]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pool = orch.fetch_all(plan, _BB)

        assert pool == []
        assert any("fetch failed" in str(w.message) for w in caught)


# ===========================================================================
# Fix 4 — BuyBox.from_ruleset: sector_exclude_match.include not dropped
# ===========================================================================

class TestBuyBoxExcludeKeywords:
    def test_sector_exclude_match_include_captured(self):
        """Anti-fit keywords under the 'include' key must reach BuyBox.sector_exclude_keywords."""
        from sourcing.models.filter_rule import (
            DiscoveryAction,
            FilterRule,
            FilterRuleset,
            ScreenTier,
        )

        rule_exclude = FilterRule(
            field="sector_exclude_match",
            group="sector",
            data_type="text",
            filter_type="keyword",
            screen_tier=ScreenTier.DISQUALIFIER,
            logic={"include": ["government defence", "public sector"]},
            scrapeable=True,
            discovery_action=DiscoveryAction.EXCLUDE,
        )
        rule_sector = FilterRule(
            field="sector_keyword_match",
            group="sector",
            data_type="text",
            filter_type="keyword",
            screen_tier=ScreenTier.SOFT,
            logic={"include": ["hvac"], "exclude": ["retail"]},
            scrapeable=True,
            discovery_action=DiscoveryAction.SCORE,
            weight=0.15,
        )
        rs = FilterRuleset(
            ruleset_id="test",
            name="test",
            base_version="v1",
            rules=[rule_sector, rule_exclude],
        )
        bb = BuyBox.from_ruleset(rs)
        # "government defence" was in sector_exclude_match.include — must be present
        assert "government defence" in bb.sector_exclude_keywords
        assert "public sector" in bb.sector_exclude_keywords
        # sector_keyword_match.exclude is also captured
        assert "retail" in bb.sector_exclude_keywords

    def test_no_double_counting_of_sector_keyword_exclude(self):
        """sector_keyword_match.exclude should appear exactly once."""
        from sourcing.models.filter_rule import (
            DiscoveryAction,
            FilterRule,
            FilterRuleset,
            ScreenTier,
        )

        rule_sector = FilterRule(
            field="sector_keyword_match",
            group="sector",
            data_type="text",
            filter_type="keyword",
            screen_tier=ScreenTier.SOFT,
            logic={"include": ["hvac"], "exclude": ["retail"]},
            scrapeable=True,
            discovery_action=DiscoveryAction.SCORE,
            weight=0.15,
        )
        rs = FilterRuleset(ruleset_id="t", name="t", base_version="v1", rules=[rule_sector])
        bb = BuyBox.from_ruleset(rs)
        assert bb.sector_exclude_keywords.count("retail") == 1


# ===========================================================================
# Fix 5 — EntityResolver.resolve returns (abn, confidence, candidate)
# ===========================================================================

class TestResolverThreeTuple:
    def _resolver(self, candidates, spine=None):
        class FakeAPI:
            def fetch(self, params):
                return candidates

        class FakeASIC:
            def lookup_abn(self, abn):
                return (spine or {}).get(abn)

        return EntityResolver(api=FakeAPI(), asic=FakeASIC())

    def test_resolve_returns_three_values(self):
        r = self._resolver([{"abn": "11111111111", "org_name": "acme", "postcode": "4000", "state": "QLD"}])
        result = r.resolve("Acme Pty Ltd", "4000", "QLD")
        assert len(result) == 3, "resolve() must return (abn, confidence, candidate)"

    def test_resolve_candidate_has_correct_abn(self):
        r = self._resolver([{"abn": "11111111111", "org_name": "acme air", "postcode": "4101", "state": "QLD"}])
        abn, rc, cand = r.resolve("Acme Air Pty Ltd", "4101", "QLD")
        assert abn == "11111111111"
        assert cand.get("abn") == "11111111111"

    def test_resolve_empty_returns_empty_dict(self):
        r = self._resolver([])
        abn, rc, cand = r.resolve("Nobody", "4000", "QLD")
        assert abn is None
        assert cand == {}

    def test_enrich_backfills_state_from_candidate(self):
        """enrich() uses the returned candidate to backfill missing state — no _last_match."""
        r = self._resolver(
            [{"abn": "11111111111", "org_name": "acme air", "postcode": "4101", "state": "QLD"}],
            spine={"11111111111": {"acn": "111", "org_name": "ACME AIR PTY LTD",
                                   "status_effective_from": "2000-01-01"}},
        )
        rec = CompanyRecord(
            entity_id="maps:test", legal_name="Acme Air Pty Ltd",
            location=Location(postcode="4101"),  # no state
        )
        r.enrich(rec)
        assert rec.location.state == "QLD"  # backfilled from candidate

    def test_concurrent_enrich_no_state_leak(self):
        """Two concurrent enrich() calls must not leak each other's candidate state."""
        candidates_by_name = {
            "Acme Air": {"abn": "11111111111", "org_name": "acme air", "postcode": "4101", "state": "QLD"},
            "NSW Co":   {"abn": "22222222222", "org_name": "nsw co",   "postcode": "2000", "state": "NSW"},
        }

        class MultiAPI:
            def fetch(self, params):
                name = params.get("name", "")
                return [v for k, v in candidates_by_name.items() if k.lower() in name.lower()]

        class EmptyASIC:
            def lookup_abn(self, abn):
                return None

        resolver = EntityResolver(api=MultiAPI(), asic=EmptyASIC())
        rec_qld = CompanyRecord(entity_id="qld", legal_name="Acme Air Pty Ltd",
                                location=Location(postcode="4101"))
        rec_nsw = CompanyRecord(entity_id="nsw", legal_name="NSW Co Pty Ltd",
                                location=Location(postcode="2000"))

        errors: list[str] = []

        def resolve_qld():
            resolver.enrich(rec_qld)
            if rec_qld.location.state and rec_qld.location.state == "NSW":
                errors.append("QLD record got NSW state")

        def resolve_nsw():
            resolver.enrich(rec_nsw)
            if rec_nsw.location.state and rec_nsw.location.state == "QLD":
                errors.append("NSW record got QLD state")

        t1 = threading.Thread(target=resolve_qld)
        t2 = threading.Thread(target=resolve_nsw)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"State leaked between concurrent enrich() calls: {errors}"


# ===========================================================================
# Fix 6 — Cache singleton
# ===========================================================================

class TestCacheSingleton:
    def setup_method(self):
        reset_default_cache()

    def teardown_method(self):
        reset_default_cache()

    def test_same_instance_returned_twice(self):
        c1 = get_default_cache()
        c2 = get_default_cache()
        assert c1 is c2

    def test_written_value_visible_on_second_call(self):
        c1 = get_default_cache()
        c1.set("k", "hello", 60)
        c2 = get_default_cache()
        assert c2.get("k") == "hello"

    def test_reset_clears_singleton(self):
        c1 = get_default_cache()
        reset_default_cache()
        c2 = get_default_cache()
        assert c1 is not c2

    def test_explicit_cache_not_affected_by_singleton(self):
        """Tests passing their own InMemoryTTLCache() are isolated."""
        singleton = get_default_cache()
        singleton.set("shared_key", "shared", 60)
        explicit = InMemoryTTLCache()
        assert explicit.get("shared_key") is None


# ===========================================================================
# Fix 7 — BulkConnector ensure_loaded thread lock
# ===========================================================================

class TestBulkConnectorThreadLock:
    def test_ensure_loaded_serialises_concurrent_calls(self, tmp_path):
        """Two threads calling ensure_loaded() concurrently must not both try to load."""
        pytest.importorskip("duckdb")
        from sourcing.connectors.base_bulk import BulkConnector

        load_count = []
        load_lock = threading.Lock()

        class TinyConnector(BulkConnector):
            source_id = "test_bulk"
            table_name = "test_table"

            def download(self):
                pass

            def load(self):
                with load_lock:
                    load_count.append(1)
                # Simulate slow load so the second thread arrives during load.
                time.sleep(0.05)
                self.conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self.table_name} (id INTEGER)"
                )
                self.conn.execute(f"INSERT INTO {self.table_name} VALUES (1)")

            def fetch(self, params):
                return []

            def normalize(self, raw):
                return CompanyRecord(entity_id="x")

        conn = TinyConnector(db_path=tmp_path / "bulk.duckdb")
        results: list[int] = []

        def worker():
            results.append(conn.ensure_loaded())

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # load() must have been called exactly once (the lock serialised the calls).
        assert load_count == [1], f"load() called {len(load_count)} times instead of 1"
        conn.close()


# ===========================================================================
# Fix 8 — SignalExtractor text truncation constant
# ===========================================================================

class TestSignalExtractorTruncation:
    def test_truncation_constant_is_8000(self):
        assert _MAX_TEXT_CHARS == 8000

    def test_long_text_uses_constant(self):
        """SignalExtractor must pass _MAX_TEXT_CHARS chars to the LLM, not 4000."""
        captured: list[str] = []

        class CaptureLLM:
            def chat(self, model, system, messages, tools=None, format=None):
                captured.append(messages[-1]["content"])
                return LLMResponse(text="{}")

        rec = CompanyRecord(
            entity_id="x", abn="1" * 11,
            website_text_raw="x" * 10000,
        )
        SignalExtractor(llm=CaptureLLM(), model="x").extract(rec, _BB)
        # The prompt must contain exactly _MAX_TEXT_CHARS x's (the text slice).
        assert "x" * _MAX_TEXT_CHARS in captured[0]
        assert "x" * (_MAX_TEXT_CHARS + 1) not in captured[0]


# ===========================================================================
# Fix 9 — deduplicate_by_abn / deduplicate_pre_resolution
# ===========================================================================

class TestDedup:
    def test_deduplicate_by_abn_keeps_richer(self):
        r1 = _co("Acme", abn="11111111111", provenance_count=1)
        r2 = _co("Acme (dup)", abn="11111111111", provenance_count=3)  # richer
        result = deduplicate_by_abn([r1, r2])
        assert len(result) == 1
        assert result[0].legal_name == "Acme (dup)"

    def test_deduplicate_by_abn_passes_unresolved(self):
        r_abn = _co("Resolved", abn="11111111111")
        r_no_abn = _co("Unresolved")
        result = deduplicate_by_abn([r_abn, r_no_abn])
        assert len(result) == 2
        names = {r.legal_name for r in result}
        assert "Unresolved" in names

    def test_deduplicate_by_abn_distinct_abns_kept(self):
        r1 = _co("A", abn="11111111111")
        r2 = _co("B", abn="22222222222")
        assert len(deduplicate_by_abn([r1, r2])) == 2

    def test_deduplicate_pre_resolution_by_name_postcode(self):
        r1 = _co("Acme Plumbing", pc="4000")
        r1.contacts_min = {"website": "http://acme.com"}  # richer
        r2 = _co("Acme Plumbing", pc="4000")  # duplicate
        result = deduplicate_pre_resolution([r1, r2])
        assert len(result) == 1
        assert result[0].contacts_min.get("website") == "http://acme.com"

    def test_deduplicate_pre_resolution_different_postcodes_kept(self):
        r1 = _co("Acme", pc="4000")
        r2 = _co("Acme", pc="2000")
        assert len(deduplicate_pre_resolution([r1, r2])) == 2

    def test_deduplicate_empty_list(self):
        assert deduplicate_by_abn([]) == []
        assert deduplicate_pre_resolution([]) == []


# ===========================================================================
# Fix 10 — EnrichmentNode uses normalize() contract
# ===========================================================================

class TestEnrichmentNodeNormalizeContract:
    def test_uses_normalize_not_raw_keys(self):
        """EnrichmentNode must call website.normalize() — not read 'markdown' directly."""
        normalize_called: list[bool] = []

        class FakeWebsite:
            def fetch(self, params):
                return [{"markdown": "We do HVAC."}]

            def normalize(self, raw):
                normalize_called.append(True)
                # This is what WebsiteFetchConnector.normalize() does:
                text = raw.get("markdown", "")
                return CompanyRecord(
                    entity_id="web:test",
                    deferred_assessment={"website_text_raw": text},
                )

        class FakeAusTender:
            def enrich_record(self, rec):
                rec.flags.append("austender_checked_no_contracts")

        rec = CompanyRecord(
            entity_id="x", abn="1" * 11,
            contacts_min={"website": "http://acme.com"},
        )
        node = EnrichmentNode(
            austender=FakeAusTender(),
            website=FakeWebsite(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_one(rec, _BB)
        assert normalize_called, "normalize() was never called — raw keys read instead"
        assert rec.website_text_raw == "We do HVAC."

    def test_deferred_assessment_text_also_read(self):
        """Text stored in deferred_assessment['website_text_raw'] must reach the record."""
        class FakeWebsite:
            def fetch(self, params):
                return [{}]
            def normalize(self, raw):
                return CompanyRecord(
                    entity_id="web:t",
                    deferred_assessment={"website_text_raw": "B2B HVAC maintenance"},
                )

        class FakeAusTender:
            def enrich_record(self, rec): pass

        rec = CompanyRecord(entity_id="x", abn="1" * 11, contacts_min={"website": "http://x.com"})
        node = EnrichmentNode(
            austender=FakeAusTender(),
            website=FakeWebsite(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_one(rec, _BB)
        assert rec.website_text_raw == "B2B HVAC maintenance"


# ===========================================================================
# Fix 11 — JudgeResult.unavailable + RankedCompany.judge_unavailable
# ===========================================================================

class TestJudgeUnavailable:
    def test_judge_result_unavailable_on_empty_data(self):
        class EmptyLLM:
            def chat(self, *a, **k):
                return LLMResponse(text="not json at all $$$$")

        jr = LLMJudge(llm=EmptyLLM(), model="x").judge(_co("A"), _BB)
        assert jr.unavailable is True
        assert jr.fit == 0.0
        assert "unavailable" in jr.rationale

    def test_judge_result_available_on_valid_data(self):
        class GoodLLM:
            def chat(self, *a, **k):
                return LLMResponse(text=json.dumps({"fit": 0.75, "rationale": "good", "standout_signals": []}))

        jr = LLMJudge(llm=GoodLLM(), model="x").judge(_co("A"), _BB)
        assert jr.unavailable is False
        assert jr.fit == 0.75

    def test_ranked_company_propagates_judge_unavailable(self):
        class UnavailableJudge:
            def chat(self, *a, **k):
                return LLMResponse(text="garbage")

        pool = [_co("Acme", abn="1" * 11)]
        pool[0].sector.keyword_density = 0.8
        ranked = rank_pool(pool, _BB, judge=LLMJudge(llm=UnavailableJudge(), model="x"), top_k=1)
        assert ranked[0].judge_unavailable is True

    def test_ranked_company_judge_available_false_by_default(self):
        class GoodLLM:
            def chat(self, *a, **k):
                return LLMResponse(text=json.dumps({"fit": 0.6, "rationale": "ok", "standout_signals": []}))

        pool = [_co("Acme", abn="1" * 11)]
        ranked = rank_pool(pool, _BB, judge=LLMJudge(llm=GoodLLM(), model="x"), top_k=1)
        assert ranked[0].judge_unavailable is False


# ===========================================================================
# Fix 12 — AusTender window default + configurable
# ===========================================================================

class TestAusTenderWindow:
    def test_default_window_is_730_days(self):
        from sourcing.connectors.austender import AusTenderConnector
        # We can't construct without the full settings, so check class-level default.
        assert AusTenderConnector.default_window_days == 730

    def test_max_pages_raised(self):
        from sourcing.connectors.austender import AusTenderConnector
        assert AusTenderConnector.max_pages == 10

    def test_settings_window_overrides_class_default(self, monkeypatch):
        monkeypatch.setenv("AUSTENDER_WINDOW_DAYS", "365")
        # Reset cached settings so the env var is picked up.
        import sourcing.config as cfg
        cfg._settings = None
        from sourcing.config import get_settings
        assert get_settings().austender_window_days == 365
        cfg._settings = None  # clean up


# ===========================================================================
# Fix 13 — AwardRegisterConnector degradation warning
# ===========================================================================

class TestAwardConnectorDegradation:
    def test_warn_on_degraded_extraction(self):
        from sourcing.connectors.awards import AwardRegisterConnector

        class DegradedConnector(AwardRegisterConnector):
            source_id = "test_awards"
            program = "Test Awards"
            program_key = "test"
            base_url_template = "https://example.com/{year}"
            category_slugs = ("cat1",)
            default_year = 2025

            def _fetch_page_markdown(self, url):
                # Five H4 headers but none matching the #### Name\n\nState Finalist pattern.
                return "\n#### Business A\n\n#### Business B\n\n#### Business C\n\n#### Business D\n\n#### Business E\n\n"

            def _classify_categories(self, blocks):
                return []

        connector = DegradedConnector(client=MagicMock(), cache=InMemoryTTLCache())

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            connector.fetch({})

        assert any("degraded" in str(w.message).lower() for w in caught), (
            "No degradation warning emitted when regex found 0 blocks from 5 H4 headers"
        )

    def test_no_warn_when_extraction_succeeds(self):
        from sourcing.connectors.awards import AwardRegisterConnector

        class GoodConnector(AwardRegisterConnector):
            source_id = "test_awards"
            program = "Test"
            program_key = "test"
            base_url_template = "https://example.com/{year}"
            category_slugs = ("cat1",)
            default_year = 2025
            max_finalists = 2

            def _fetch_page_markdown(self, url):
                return (
                    "\n#### Acme Testing\n\nQLD Finalist\n\nThey do testing.\n"
                    "#### Bravo HVAC\n\nNSW Finalist\n\nAir conditioning.\n"
                )

            def _classify_categories(self, blocks):
                return ["testing services", "hvac"]

        connector = GoodConnector(client=MagicMock(), cache=InMemoryTTLCache())

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = connector.fetch({})

        degraded_warnings = [w for w in caught if "degraded" in str(w.message).lower()]
        assert len(degraded_warnings) == 0
        assert len(records) == 2


# ===========================================================================
# Fix 14 — entity_id collision hash-fallback
# ===========================================================================

class TestEntityIdCollision:
    def test_google_maps_hash_fallback_when_no_place_id(self):
        from sourcing.connectors.google_maps import GoogleMapsConnector
        raw = {
            "title": "Smith Plumbing",
            "postalCode": "4000",
            "state": "QLD",
            "categories": ["Plumber"],
        }
        rec = GoogleMapsConnector().normalize(raw)
        assert rec.entity_id.startswith("maps:")
        assert "hash:" in rec.entity_id
        assert "Smith Plumbing" not in rec.entity_id  # not just the bare name

    def test_google_maps_two_businesses_same_name_different_entity_ids(self):
        from sourcing.connectors.google_maps import GoogleMapsConnector
        c = GoogleMapsConnector()
        r1 = c.normalize({"title": "Acme Co", "postalCode": "4000", "state": "QLD", "categories": []})
        r2 = c.normalize({"title": "Acme Co", "postalCode": "2000", "state": "NSW", "categories": []})
        assert r1.entity_id != r2.entity_id

    def test_google_maps_stable_place_id_unchanged(self):
        from sourcing.connectors.google_maps import GoogleMapsConnector
        raw = {"placeId": "ChIJ_abc123", "title": "Energy Evolution", "categories": []}
        rec = GoogleMapsConnector().normalize(raw)
        assert rec.entity_id == "maps:ChIJ_abc123"

    def test_yellow_pages_hash_fallback(self):
        from sourcing.connectors.yellow_pages import YellowPagesConnector
        raw = {"name": "Smith Plumbing", "category": "Plumber", "address": "4 Main St QLD 4000"}
        rec = YellowPagesConnector().normalize(raw)
        assert rec.entity_id.startswith("yp:")
        assert "hash:" in rec.entity_id

    def test_yellow_pages_two_same_name_different_postcodes(self):
        from sourcing.connectors.yellow_pages import YellowPagesConnector
        c = YellowPagesConnector()
        # Pass explicit postcode/state so the hash key differs (address is parsed separately)
        r1 = c.normalize({"name": "Acme", "postcode": "4000", "state": "QLD"})
        r2 = c.normalize({"name": "Acme", "postcode": "2000", "state": "NSW"})
        assert r1.entity_id != r2.entity_id

    def test_yellow_pages_url_takes_priority(self):
        from sourcing.connectors.yellow_pages import YellowPagesConnector
        raw = {"name": "Acme", "url": "https://www.yellowpages.com.au/find/acme"}
        rec = YellowPagesConnector().normalize(raw)
        assert "hash:" not in rec.entity_id
        assert rec.entity_id.startswith("yp:")


# ===========================================================================
# Fix 15 — EnrichmentNode concurrent enrichment
# ===========================================================================

class TestConcurrentEnrichment:
    def test_enrich_pool_concurrent_all_records_processed(self):
        """max_workers > 1 must process all resolved records."""
        processed: list[str] = []
        lock = threading.Lock()

        class FakeAusTender:
            def enrich_record(self, rec):
                with lock:
                    processed.append(rec.abn)

        records = [_co(f"Co{i}", abn=f"{'1' * 10}{i}") for i in range(5)]
        node = EnrichmentNode(
            austender=FakeAusTender(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_pool(records, _BB, max_workers=3)
        assert len(processed) == 5

    def test_enrich_pool_sequential_by_default(self):
        """Default (max_workers=None) still processes all records."""
        processed: list[str] = []

        class FakeAusTender:
            def enrich_record(self, rec):
                processed.append(rec.abn)

        records = [_co(f"Co{i}", abn=f"{'1' * 10}{i}") for i in range(3)]
        node = EnrichmentNode(
            austender=FakeAusTender(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_pool(records, _BB)
        assert len(processed) == 3


# ===========================================================================
# Fix 16 — _RateLimiter thread-safe acquire()
# ===========================================================================

class TestRateLimiterThreadSafe:
    def test_concurrent_acquire_honours_rate(self):
        from sourcing.connectors.base_api import _RateLimiter

        timestamps: list[float] = []
        real_time = time.monotonic

        def recording_clock():
            return real_time()

        limiter = _RateLimiter(10.0, clock=recording_clock, sleep=time.sleep)

        def acquire_and_record():
            limiter.acquire()
            timestamps.append(real_time())

        threads = [threading.Thread(target=acquire_and_record) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # With 10 rps → 100ms minimum interval. 5 acquires should span ≥ 400ms total.
        timestamps.sort()
        span = timestamps[-1] - timestamps[0]
        assert span >= 0.35, f"Rate limiter too loose: span={span:.3f}s for 5 acquires at 10 rps"

    def test_acquire_is_safe_under_threads_no_exception(self):
        from sourcing.connectors.base_api import _RateLimiter

        limiter = _RateLimiter(100.0)  # fast, just check no exception
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    limiter.acquire()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ===========================================================================
# Fix 17 — ConnectorRegistry singleton / get_or_create
# ===========================================================================

class TestConnectorRegistry:
    def setup_method(self):
        ConnectorRegistry.reset()

    def teardown_method(self):
        ConnectorRegistry.reset()

    def test_get_returns_same_instance(self):
        r1 = ConnectorRegistry.get()
        r2 = ConnectorRegistry.get()
        assert r1 is r2

    def test_get_or_create_returns_same_connector(self):
        registry = ConnectorRegistry()

        class FakeConnector:
            pass

        with patch("sourcing.connectors.loader.load_connector") as mock_load:
            mock_load.return_value = FakeConnector()
            c1 = registry.get_or_create("sourcing.connectors.google_maps.GoogleMapsConnector")
            c2 = registry.get_or_create("sourcing.connectors.google_maps.GoogleMapsConnector")

        assert c1 is c2
        assert mock_load.call_count == 1  # instantiated only once

    def test_clear_evicts_cache(self):
        registry = ConnectorRegistry()
        with patch("sourcing.connectors.loader.load_connector") as mock_load:
            mock_load.return_value = object()
            registry.get_or_create("ref.A")
            registry.clear()
            registry.get_or_create("ref.A")
        assert mock_load.call_count == 2  # had to re-instantiate after clear

    def test_reset_replaces_singleton(self):
        r1 = ConnectorRegistry.get()
        ConnectorRegistry.reset()
        r2 = ConnectorRegistry.get()
        assert r1 is not r2

    def test_thread_safe_get_or_create(self):
        """Concurrent get_or_create for the same ref must instantiate exactly once."""
        registry = ConnectorRegistry()
        call_count: list[int] = []

        class Counter:
            pass

        def slow_load(ref, **kw):
            time.sleep(0.02)
            call_count.append(1)
            return Counter()

        with patch("sourcing.connectors.loader.load_connector", side_effect=slow_load):
            threads = [
                threading.Thread(
                    target=lambda: registry.get_or_create("some.Connector")
                )
                for _ in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert sum(call_count) == 1, f"load_connector called {sum(call_count)} times instead of 1"


# ===========================================================================
# Fix 18 — EnrichmentNode checkpoint callback
# ===========================================================================

class TestEnrichmentCheckpoint:
    def test_checkpoint_called_per_enriched_record(self):
        checkpointed: list[str] = []

        class FakeAusTender:
            def enrich_record(self, rec):
                pass

        records = [_co(f"Co{i}", abn=f"{'1' * 10}{i}") for i in range(3)]
        node = EnrichmentNode(
            austender=FakeAusTender(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_pool(records, _BB, checkpoint=lambda r: checkpointed.append(r.abn))
        assert len(checkpointed) == 3
        assert set(checkpointed) == {r.abn for r in records}

    def test_checkpoint_called_after_each_record_not_at_end(self):
        """Checkpoint fires after each record, not as a batch at the end."""
        order: list[str] = []

        class SlowAusTender:
            def enrich_record(self, rec):
                order.append(f"enrich:{rec.abn}")

        records = [_co("A", abn="1" * 11), _co("B", abn="2" * 11)]
        node = EnrichmentNode(
            austender=SlowAusTender(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )
        node.enrich_pool(
            records, _BB,
            checkpoint=lambda r: order.append(f"checkpoint:{r.abn}"),
        )
        # enrich then checkpoint, enrich then checkpoint — interleaved.
        for i in range(0, len(order) - 1, 2):
            assert order[i].startswith("enrich"), f"expected enrich at index {i}"
            assert order[i + 1].startswith("checkpoint"), f"expected checkpoint at index {i+1}"

    def test_checkpoint_with_concurrent_enrichment(self):
        checkpointed: list[str] = []
        lock = threading.Lock()

        class FakeAusTender:
            def enrich_record(self, rec):
                pass

        records = [_co(f"Co{i}", abn=f"{'1' * 10}{i}") for i in range(6)]
        node = EnrichmentNode(
            austender=FakeAusTender(),
            signal_extractor=SignalExtractor(
                llm=MagicMock(chat=lambda *a, **k: LLMResponse(text="{}")), model="x"
            ),
        )

        def safe_checkpoint(r):
            with lock:
                checkpointed.append(r.abn)

        node.enrich_pool(records, _BB, max_workers=3, checkpoint=safe_checkpoint)
        assert len(checkpointed) == 6
