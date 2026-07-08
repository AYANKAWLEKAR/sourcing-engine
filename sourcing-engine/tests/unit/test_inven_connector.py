"""Inven MCP connector + pe_vc honesty + proxy survivorship (W2). No network."""
from __future__ import annotations

from sourcing.connectors.inven import InvenConnector
from sourcing.enrichment.proxy_estimator import ProxyEstimator
from sourcing.models.company import CompanyRecord, Ownership, Sector, Size
from sourcing.models.ranking import RankedCompany
from sourcing.rank.rank import deferred_items
from sourcing.runs.shortlist_gate import ShortlistGate


def _fake_caller(payload):
    def call(server, tool, arguments):
        assert tool == "search_companies"
        return payload
    return call


_HIT = {"companies": [{
    "abn": "51824753556", "name": "Acme Air Pty Ltd",
    "pe_vc_backed": True, "institutional_investors": True, "revenue_aud": 5_000_000,
}]}


class TestInvenConnector:
    def test_fetch_and_normalize(self):
        c = InvenConnector(tool_caller=_fake_caller(_HIT))
        raws = c.fetch({"abn": "51824753556"})
        assert len(raws) == 1
        rec = c.normalize(raws[0])
        assert rec.ownership.pe_vc_backed is True
        assert rec.ownership.institutional_on_register is True
        assert rec.size.revenue_est_aud == 5_000_000
        assert rec.size.revenue_confidence == 0.9
        assert any(p.source == "inven" for p in rec.provenance)

    def test_fetch_needs_a_query_key(self):
        c = InvenConnector(tool_caller=_fake_caller(_HIT))
        assert c.fetch({}) == []

    def test_enrich_record_merges_ownership_and_revenue(self):
        c = InvenConnector(tool_caller=_fake_caller(_HIT))
        rec = CompanyRecord(entity_id="x", abn="51824753556", legal_name="Acme Air")
        c.enrich_record(rec)
        assert rec.ownership.pe_vc_backed is True
        assert rec.size.revenue_est_aud == 5_000_000

    def test_not_configured_degrades_honestly(self):
        c = InvenConnector(tool_caller=None)  # no MCP transport
        rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme")
        c.enrich_record(rec)  # must not raise
        assert "unverified:ownership:inven_not_configured" in rec.flags
        assert rec.ownership.pe_vc_backed is None

    def test_no_match_flagged(self):
        c = InvenConnector(tool_caller=_fake_caller({"companies": []}))
        rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Nobody")
        c.enrich_record(rec)
        assert "inven_checked_no_match" in rec.flags

    def test_from_settings_if_available_none_without_creds(self):
        # Default settings have no Inven creds → None.
        assert InvenConnector.from_settings_if_available() is None


class TestPeVcHonesty:
    def test_unknown_pe_vc_surfaced_in_checklist(self):
        rec = CompanyRecord(entity_id="x", ownership=Ownership(pe_vc_backed=None))
        items = deferred_items(rec)
        assert any("PE/VC backing not checked" in i for i in items)

    def test_verified_independent_not_surfaced(self):
        rec = CompanyRecord(entity_id="x", ownership=Ownership(pe_vc_backed=False))
        items = deferred_items(rec)
        assert not any("PE/VC backing not checked" in i for i in items)

    def test_operating_entity_flag_gets_friendly_wording(self):
        rec = CompanyRecord(entity_id="x", flags=["unverified:operating_entity"])
        items = deferred_items(rec)
        assert any("operating business" in i for i in items)
        assert "unverified:operating_entity" not in items  # translated, not raw


class TestProxySurvivorship:
    def test_direct_revenue_survives_proxy(self):
        # A direct high-confidence revenue (Inven) must not be overwritten by the proxy.
        rec = CompanyRecord(entity_id="x", abn="1" * 11,
                            size=Size(employee_count=20, revenue_est_aud=5_000_000, revenue_confidence=0.9),
                            sector=Sector(anzsic=["3223"], anzsic_confidence=0.7))
        ProxyEstimator().estimate(rec)
        assert rec.size.revenue_est_aud == 5_000_000  # untouched
        assert rec.size.revenue_confidence == 0.9

    def test_proxy_fills_when_no_direct_estimate(self):
        rec = CompanyRecord(entity_id="x", abn="1" * 11,
                            size=Size(employee_count=20),
                            sector=Sector(anzsic=["3223"], anzsic_confidence=0.7))
        ProxyEstimator().estimate(rec)
        assert rec.size.revenue_est_aud is not None  # proxy filled it


class TestGateWiring:
    def test_gate_calls_inven_on_topn(self):
        class FakeInven:
            def __init__(self):
                self.seen = []

            def enrich_record(self, rec):
                self.seen.append(rec.abn)
                rec.ownership.pe_vc_backed = False
                return rec

        inven = FakeInven()
        gate = ShortlistGate([], inven=inven, top_n=2)
        shortlist = [
            RankedCompany(record=CompanyRecord(entity_id=f"x{i}", abn=str(i) * 11),
                          s_stat=50.0, s_final=0.5)
            for i in range(3)
        ]
        gate.apply(shortlist)
        assert len(inven.seen) == 2  # top_n only
        assert shortlist[0].record.ownership.pe_vc_backed is False
