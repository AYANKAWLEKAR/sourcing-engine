"""Tests for the persistent connector cache + ABN-keyed CompanyRecord cache."""
from __future__ import annotations

from sourcing.connectors.cache import SqliteCache
from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.enrichment.record_cache import CompanyRecordCache, apply_cached_enrichment
from sourcing.enrichment.signal_extractor import SignalExtractor
from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location, MoatSignals, Ownership, Provenance
from sourcing.rank.buybox import BuyBox

_BB = BuyBox(thesis="HVAC", sector_keywords=["hvac"], states=["QLD"])
_SIGNAL_JSON = '{"keyword_hits":["hvac"],"exclude_hits":[],"keyword_density":0.5,' \
    '"business_model":"B2B","moat_signals":{},"anzsic_guess":"3223","anzsic_confidence":0.6}'


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, model, system, messages, tools=None, format=None):
        self.calls += 1
        return LLMResponse(text=_SIGNAL_JSON)


# ---------------------------------------------------------------------------
# SqliteCache — persistence across instances
# ---------------------------------------------------------------------------

class TestSqliteCache:
    def test_persists_across_instances(self, tmp_path):
        p = str(tmp_path / "cache.sqlite")
        SqliteCache(p).set("k", {"a": 1}, 3600)
        assert SqliteCache(p).get("k") == {"a": 1}  # fresh instance, same file

    def test_expiry(self, tmp_path):
        clock = [1000.0]
        c = SqliteCache(str(tmp_path / "c.sqlite"), clock=lambda: clock[0])
        c.set("k", "v", 60)
        assert c.get("k") == "v"
        clock[0] += 61
        assert c.get("k") is None

    def test_miss_returns_none(self, tmp_path):
        assert SqliteCache(str(tmp_path / "c.sqlite")).get("absent") is None


# ---------------------------------------------------------------------------
# CompanyRecordCache
# ---------------------------------------------------------------------------

def _enriched(abn: str) -> CompanyRecord:
    return CompanyRecord(
        entity_id=f"abn:{abn}", abn=abn, legal_name="Acme Air",
        location=Location(state="QLD"),
        website_text_raw="We provide commercial HVAC services.",
        moat_signals=MoatSignals(ip=True, ip_count=2, ip_types=["patent"], gov_contracts=False),
        ownership=Ownership(listed_entity=None),
        provenance=[Provenance(field="moat_signals.ip", source="ipgod", confidence=0.9)],
        flags=["ipgod_checked_no_ip", "austender_checked_no_contracts"],
    )


class TestCompanyRecordCache:
    def test_put_get_roundtrip(self, tmp_path):
        c = CompanyRecordCache(str(tmp_path / "c.sqlite"), ttl_seconds=3600)
        c.put(_enriched("11111111111"))
        got = c.get("11111111111")
        assert got is not None
        assert got.moat_signals.ip_count == 2
        assert got.website_text_raw

    def test_persists_across_instances(self, tmp_path):
        p = str(tmp_path / "c.sqlite")
        CompanyRecordCache(p, ttl_seconds=3600).put(_enriched("22222222222"))
        assert CompanyRecordCache(p, ttl_seconds=3600).get("22222222222") is not None

    def test_ttl_expiry(self, tmp_path):
        clock = [1000.0]
        c = CompanyRecordCache(str(tmp_path / "c.sqlite"), ttl_seconds=60, clock=lambda: clock[0])
        c.put(_enriched("33333333333"))
        assert c.get("33333333333") is not None
        clock[0] += 61
        assert c.get("33333333333") is None

    def test_put_without_abn_is_noop(self, tmp_path):
        c = CompanyRecordCache(str(tmp_path / "c.sqlite"), ttl_seconds=3600)
        c.put(CompanyRecord(entity_id="x"))  # no abn
        assert c.get("") is None


def test_apply_cached_enrichment_overlays_external_signals():
    cached = _enriched("11111111111")
    cached.moat_signals.gov_contracts = True
    cached.moat_signals.gov_contract_value_aud = 500_000
    target = CompanyRecord(entity_id="maps:x", abn="11111111111", legal_name="Acme (Maps)",
                           location=Location(state="QLD", postcode="4000"))
    apply_cached_enrichment(target, cached)
    # external signals copied
    assert target.moat_signals.ip is True
    assert target.moat_signals.ip_count == 2
    assert target.moat_signals.gov_contracts is True
    assert target.website_text_raw
    assert "ipgod_checked_no_ip" in target.flags
    # identity/discovery fields untouched
    assert target.legal_name == "Acme (Maps)"
    assert target.location.postcode == "4000"


# ---------------------------------------------------------------------------
# EnrichmentNode cache-hit wiring
# ---------------------------------------------------------------------------

class RecordingConnector:
    def __init__(self):
        self.calls = 0

    def enrich_record(self, rec):
        self.calls += 1
        return rec


class RecordingWebsite:
    def __init__(self):
        self.fetches = 0

    def fetch(self, params):
        self.fetches += 1
        return [{"markdown": "We provide commercial HVAC and refrigeration services to businesses."}]

    def normalize(self, raw):
        return CompanyRecord(entity_id="w", website_text_raw=raw["markdown"])


def test_enrichment_cache_miss_then_hit_skips_external_calls(tmp_path):
    cache = CompanyRecordCache(str(tmp_path / "c.sqlite"), ttl_seconds=3600)
    austender, ipgod, asx = RecordingConnector(), RecordingConnector(), RecordingConnector()
    website = RecordingWebsite()
    llm = FakeLLM()
    node = EnrichmentNode(
        austender=austender, website=website, ipgod=ipgod, asx=asx,
        signal_extractor=SignalExtractor(llm=llm, model="x"), record_cache=cache,
    )

    rec1 = CompanyRecord(entity_id="maps:1", abn="99999999999", legal_name="Acme",
                         location=Location(state="QLD"),
                         contacts_min={"website": "http://acme.com.au"})
    node.enrich_one(rec1, _BB)
    # First pass (miss): all external calls ran, record stored.
    assert austender.calls == 1 and ipgod.calls == 1 and asx.calls == 1
    assert website.fetches == 1
    assert cache.get("99999999999") is not None

    # Second pass (hit): external calls skipped, signal extractor still runs.
    rec2 = CompanyRecord(entity_id="maps:2", abn="99999999999", legal_name="Acme",
                         location=Location(state="QLD"),
                         contacts_min={"website": "http://acme.com.au"})
    llm_calls_before = llm.calls
    node.enrich_one(rec2, _BB)
    assert austender.calls == 1 and ipgod.calls == 1 and asx.calls == 1  # unchanged
    assert website.fetches == 1                                          # no Apify re-fetch
    assert "enrichment_cache_hit" in rec2.flags
    assert llm.calls == llm_calls_before + 1                             # keyword extraction re-ran


def test_enrichment_no_cache_still_works(tmp_path):
    node = EnrichmentNode(
        austender=RecordingConnector(), website=RecordingWebsite(),
        signal_extractor=SignalExtractor(llm=FakeLLM(), model="x"),
    )
    rec = CompanyRecord(entity_id="x", abn="1" * 11, location=Location(state="QLD"),
                        contacts_min={"website": "http://x.com"})
    node.enrich_one(rec, _BB)
    assert "enrichment_cache_hit" not in rec.flags
