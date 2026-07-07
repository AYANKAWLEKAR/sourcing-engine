"""Offline unit tests for the Trades Champion AgentConnector.

No network / no LLM: the Apify client returns fixed page markdown and the LLM
returns a fixed ``{"businesses": [...]}`` extraction. Covers extraction,
full-state-name normalization, winner/finalist level, dedup across pages, and
the award-signal normalize.
"""
from __future__ import annotations

import json

from sourcing.connectors.awards import TradesChampionConnector, _norm_state
from sourcing.connectors.cache import InMemoryTTLCache
from sourcing.llm import LLMResponse

_TRADES_MD = """
# Australian Trades Small Business Champion Awards — Finalists

| Category | Business | Suburb | State |
| Air Conditioning | Millair Climate Control | West End | Queensland |
| Plumber - Large | Reed Plumbing and Drainage | Melbourne | Victoria |
| Electrical Business | GESA Electrical | Adelaide | South Australia |
"""

_BUSINESSES_JSON = json.dumps({
    "businesses": [
        {"name": "Millair Climate Control", "state": "Queensland",
         "category": "air conditioning", "level": "winner"},
        {"name": "Reed Plumbing and Drainage", "state": "VIC",
         "category": "plumbing", "level": "finalist"},
        {"name": "GESA Electrical", "state": "South Australia",
         "category": "electrical", "level": "finalist"},
    ]
})


class FakeApify:
    def __init__(self, markdown):
        self._md = markdown

    def actor(self, actor_id):
        class _A:
            def call(self, run_input, **kw):
                class _R:
                    default_dataset_id = "ds1"

                return _R()

        return _A()

    def dataset(self, dsid):
        md = self._md

        class _D:
            def list_items(self):
                class _Res:
                    items = [{"markdown": md}]

                return _Res()

        return _D()


class FakeLLM:
    def __init__(self, json_text):
        self._text = json_text
        self.calls = 0

    def chat(self, model, system, messages, tools=None, format=None):
        self.calls += 1
        return LLMResponse(text=self._text)


def _connector(markdown=_TRADES_MD, businesses_json=_BUSINESSES_JSON) -> TradesChampionConnector:
    return TradesChampionConnector(
        cache=InMemoryTTLCache(),
        client=FakeApify(markdown),
        llm_client=FakeLLM(businesses_json),
    )


class TestNormState:
    def test_full_names_map_to_abbrev(self):
        assert _norm_state("Queensland") == "QLD"
        assert _norm_state("New South Wales") == "NSW"
        assert _norm_state("South Australia") == "SA"

    def test_abbrev_passthrough_and_unknown(self):
        assert _norm_state("VIC") == "VIC"
        assert _norm_state("Overseas") is None


class TestFetch:
    def test_extracts_businesses_from_one_url(self):
        c = _connector()
        recs = c.fetch({"urls": ["https://example.com/finalists"]})
        assert len(recs) == 3
        names = {r["org_name"] for r in recs}
        assert "Millair Climate Control" in names

    def test_full_state_name_normalized(self):
        c = _connector()
        recs = c.fetch({"urls": ["https://x"]})
        millair = next(r for r in recs if r["org_name"] == "Millair Climate Control")
        assert millair["state"] == "QLD"  # "Queensland" → QLD

    def test_winner_vs_finalist_level(self):
        c = _connector()
        recs = c.fetch({"urls": ["https://x"]})
        millair = next(r for r in recs if r["org_name"] == "Millair Climate Control")
        reed = next(r for r in recs if r["org_name"] == "Reed Plumbing and Drainage")
        assert millair["raw"]["level"] == "winner"
        assert reed["raw"]["level"] == "finalist"

    def test_dedup_across_multiple_urls(self):
        c = _connector()
        # Same businesses returned for each of 3 URLs → deduped to 3 unique.
        recs = c.fetch({"urls": ["https://a", "https://b", "https://c"]})
        assert len(recs) == 3

    def test_empty_extraction_returns_nothing(self):
        c = _connector(businesses_json='{"businesses": []}')
        assert c.fetch({"urls": ["https://x"]}) == []

    def test_malformed_llm_output_no_crash(self):
        c = _connector(businesses_json="not json")
        assert c.fetch({"urls": ["https://x"]}) == []


class TestNormalize:
    def test_winner_normalize_sets_award_and_category(self):
        c = _connector()
        recs = c.fetch({"urls": ["https://x"]})
        millair = next(r for r in recs if r["org_name"] == "Millair Climate Control")
        rec = c.normalize(millair)
        assert rec.entity_id.startswith("award:trades_champion:")
        assert rec.abn is None  # resolved downstream
        assert rec.moat_signals.award_finalist is True
        assert rec.location.state == "QLD"
        assert rec.sector.category_text == ["air conditioning"]
        assert len(rec.award_signals) == 1
        assert rec.award_signals[0].level == "winner"
        assert rec.award_signals[0].program == "Australian Trades Small Business Champion"
        assert rec.award_signals[0].tier == 1

    def test_provenance_confidence_split(self):
        c = _connector()
        rec = c.normalize(c.fetch({"urls": ["https://x"]})[0])
        award_prov = next(p for p in rec.provenance if p.field == "award_finalist")
        sector_prov = next(p for p in rec.provenance if p.field == "sector")
        assert award_prov.confidence == 0.9   # verbatim page fact
        assert sector_prov.confidence == 0.5   # softer sector hint
