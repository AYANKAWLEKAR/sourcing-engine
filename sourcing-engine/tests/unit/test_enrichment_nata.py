from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.models.company import CompanyRecord, Location
from sourcing.rank.buybox import BuyBox


class _FakeExtractor:
    def extract(self, rec, buybox):  # no-op signal extractor
        return rec


class _FakeAusTender:
    def enrich_record(self, rec):
        return rec


class _StubNataCache:
    def find_by_normalized_name(self, name, state=None):
        if "acme" in name.lower():
            return {"legal_name": "Acme Testing Pty Ltd", "primary_state": "NSW",
                    "nata_site_count": 4, "nata_service_types": ["testing"],
                    "nata_accreditation_numbers": ["2771"], "nata_states": ["NSW", "VIC"],
                    "nata_multistate": True}
        return None


def _node(nata_cache):
    return EnrichmentNode(austender=_FakeAusTender(), website=None,
                          signal_extractor=_FakeExtractor(), nata_cache=nata_cache)


def _bb():
    return BuyBox(thesis="testing")


def test_plan_b_annotates_hit():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Testing Pty Ltd",
                        location=Location(state="NSW"))
    _node(_StubNataCache()).enrich_one(rec, _bb())
    assert rec.moat_signals.nata_accreditation is True
    assert rec.moat_signals.regulatory_accreditation is True
    assert rec.moat_signals.nata_site_count == 4
    assert any(p.source == "nata_cache" for p in rec.provenance)


def test_plan_b_miss_is_noop():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Unrelated Co",
                        location=Location(state="NSW"))
    _node(_StubNataCache()).enrich_one(rec, _bb())
    assert rec.moat_signals.nata_accreditation is False


def test_plan_b_absent_cache_is_noop():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Testing Pty Ltd",
                        location=Location(state="NSW"))
    _node(None).enrich_one(rec, _bb())  # nata_cache=None
    assert rec.moat_signals.nata_accreditation is False
