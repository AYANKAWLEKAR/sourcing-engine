from sourcing.connectors.nata import NATACache
from sourcing.models.company import CompanyRecord, Location, MoatSignals


def _rec(name, state="NSW"):
    return CompanyRecord(entity_id=f"nata:{name}", legal_name=name,
                         location=Location(state=state),
                         moat_signals=MoatSignals(nata_accreditation=True,
                                                  nata_site_count=2, nata_states=[state]))


def test_cache_roundtrip(tmp_path):
    cache = NATACache(tmp_path / "nata.duckdb")
    cache.upsert([_rec("Acme Testing Pty Ltd")])
    hit = cache.find_by_normalized_name("Acme Testing Pty. Ltd.")  # suffix/case differ
    assert hit is not None
    assert hit["nata_site_count"] == 2
    assert cache.find_by_normalized_name("Unknown Co") is None
