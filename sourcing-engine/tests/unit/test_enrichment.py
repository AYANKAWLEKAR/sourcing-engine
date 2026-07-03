"""Unit tests for Part A enrichment — signal extractor, proxy estimator, node.

All offline: the LLM is a fake returning fixed JSON; connectors are fakes.
"""
from __future__ import annotations

import json

from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.enrichment.proxy_estimator import ProxyEstimator
from sourcing.enrichment.signal_extractor import SignalExtractor
from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location
from sourcing.rank.buybox import BuyBox


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0

    def chat(self, model, system, messages, tools=None, format=None):
        self.calls += 1
        return LLMResponse(text=json.dumps(self._payload))


_BUYBOX = BuyBox(thesis="HVAC", sector_keywords=["hvac", "air conditioning"],
                 sector_exclude_keywords=["retail"], states=["QLD"], target_models=["B2B"])

_SIGNAL_JSON = {
    "keyword_hits": ["hvac", "air conditioning"],
    "exclude_hits": [],
    "keyword_density": 0.6,
    "business_model": "B2B",
    "moat_signals": {"physical_ops": True, "regulatory_accreditation": True,
                     "hard_assets": True, "recurring_revenue_hint": False},
    "anzsic_guess": "3223", "anzsic_confidence": 0.7,
}


# ---------------------------------------------------------------------------
# Signal extractor
# ---------------------------------------------------------------------------

def _record_with_text(text: str) -> CompanyRecord:
    return CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Air",
                         website_text_raw=text, location=Location(state="QLD"))


def test_signal_extractor_maps_b2b():
    rec = _record_with_text("We provide commercial HVAC and air conditioning installation for businesses.")
    SignalExtractor(llm=FakeLLM(_SIGNAL_JSON), model="x").extract(rec, _BUYBOX)
    assert rec.business_model == "B2B"
    assert rec.sector.keyword_hits == ["hvac", "air conditioning"]
    assert rec.sector.keyword_density == 0.6
    assert rec.sector.anzsic == ["3223"]
    assert rec.moat_signals.regulatory_accreditation is True
    assert any(p.source == "signal_extractor" for p in rec.provenance)


def test_signal_extractor_populates_exclude_hits():
    payload = dict(_SIGNAL_JSON, exclude_hits=["retail storefront"])
    rec = _record_with_text("We run a retail storefront selling air conditioners to the public.")
    SignalExtractor(llm=FakeLLM(payload), model="x").extract(rec, _BUYBOX)
    assert rec.sector.exclude_hits == ["retail storefront"]


def test_signal_extractor_flags_empty_text_no_crash():
    rec = _record_with_text("")
    llm = FakeLLM(_SIGNAL_JSON)
    SignalExtractor(llm=llm, model="x").extract(rec, _BUYBOX)
    assert "unverified:sector:no_website_text" in rec.flags
    assert llm.calls == 0  # never called the model


def test_signal_extractor_handles_unparseable():
    class BadLLM:
        def chat(self, *a, **k):
            return LLMResponse(text="not json")

    rec = _record_with_text("Some real website text about HVAC services for businesses.")
    SignalExtractor(llm=BadLLM(), model="x").extract(rec, _BUYBOX)
    assert "unverified:sector:extract_failed" in rec.flags


# ---------------------------------------------------------------------------
# Proxy estimator
# ---------------------------------------------------------------------------

def test_proxy_estimator_computes_band():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme")
    rec.size.employee_count = 20
    rec.sector.anzsic = ["3223"]
    rec.sector.anzsic_confidence = 0.8
    ProxyEstimator().estimate(rec)
    assert rec.size.revenue_est_aud and rec.size.revenue_est_aud > 0
    assert rec.size.ebitda_est_aud and rec.size.ebitda_est_aud > 0
    assert rec.size.ebitda_confidence is not None and rec.size.ebitda_confidence <= 0.4  # capped


def test_proxy_estimator_flags_no_employees():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme")
    ProxyEstimator().estimate(rec)
    assert "unverified:ebitda_aud:no_employee_count" in rec.flags
    assert rec.size.ebitda_est_aud is None


# ---------------------------------------------------------------------------
# Enrichment node
# ---------------------------------------------------------------------------

class FakeAusTender:
    def __init__(self):
        self.seen = []

    def enrich_record(self, rec):
        self.seen.append(rec.abn)
        rec.flags.append("austender_checked_no_contracts")
        return rec


class FakeWebsite:
    def fetch(self, params):
        return [{"markdown": "We deliver commercial HVAC services to businesses across QLD."}]

    def normalize(self, raw):
        from sourcing.models.company import CompanyRecord
        text = raw.get("markdown", "")
        return CompanyRecord(entity_id="web:fake", website_text_raw=text)


def test_enrichment_node_runs_waterfall():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Air",
                        contacts_min={"website": "http://acme.com.au"}, location=Location(state="QLD"))
    node = EnrichmentNode(
        austender=FakeAusTender(),
        website=FakeWebsite(),
        signal_extractor=SignalExtractor(llm=FakeLLM(_SIGNAL_JSON), model="x"),
    )
    node.enrich_pool([rec], _BUYBOX)
    assert "austender_checked_no_contracts" in rec.flags   # austender ran
    assert rec.website_text_raw                             # text fetched
    assert rec.business_model == "B2B"                      # signals extracted


def test_enrichment_node_skips_unresolved():
    rec = CompanyRecord(entity_id="x", legal_name="No ABN Co")  # no abn
    austender = FakeAusTender()
    node = EnrichmentNode(austender=austender, website=FakeWebsite(),
                          signal_extractor=SignalExtractor(llm=FakeLLM(_SIGNAL_JSON), model="x"))
    node.enrich_pool([rec], _BUYBOX)
    assert austender.seen == []  # never touched the unresolved record
