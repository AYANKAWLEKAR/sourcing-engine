"""Live micro-sourcing acceptance test (plan §8) — the off-market proof.

A complete off-market discovery run on one tiny buy-box slice: no ABNs in,
spine-anchored CompanyRecords out. Needs APIFY_API_TOKEN + ABN_LOOKUP_GUID +
ASIC_CSV_PATH.

    buy-box slice -> Maps scrape (category+location, NO abn)
                  -> EntityResolver (name+postcode -> abn via ABN Lookup)
                  -> ASIC spine merge (abn -> legal name, registration date)
                  -> CompanyRecord with provenance
"""
from __future__ import annotations

import pytest

from sourcing.connectors.cache import InMemoryTTLCache
from sourcing.connectors.google_maps import GoogleMapsConnector
from sourcing.enrichment.entity_resolution import EntityResolver

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def sourced(require_apify_token, require_abn_guid, require_asic_csv):
    maps = GoogleMapsConnector(cache=InMemoryTTLCache())
    resolver = EntityResolver()

    # 1. discover off-market candidates by category + place (no identifiers in)
    raw = maps.fetch({
        "search_terms": ["HVAC installer", "air conditioning services"],
        "location": "Brisbane QLD Australia",
        "max_places": 50,
    })
    records = [maps.normalize(r) for r in raw]

    # 2. anchor each to the ABN spine
    for rec in records:
        resolver.enrich(rec)

    resolver.asic.close()
    return records


def test_discovered_at_least_20(sourced):
    assert len(sourced) >= 20, f"only {len(sourced)} candidates discovered"


def test_resolution_rate_at_least_60pct(sourced):
    resolved = [r for r in sourced if r.abn]
    rate = len(resolved) / len(sourced)
    assert rate >= 0.60, f"resolution rate {rate:.0%} too low ({len(resolved)}/{len(sourced)})"


def test_resolved_records_carry_spine_and_signal(sourced):
    resolved = [r for r in sourced if r.abn]
    assert resolved
    sample = next(r for r in resolved if r.age.asic_registered)  # at least one merged spine date
    assert sample.legal_name
    assert sample.sector.category_text  # the Maps category signal survived
    assert sample.provenance            # every field traceable


def test_print_summary(sourced):
    resolved = [r for r in sourced if r.abn]
    rate = len(resolved) / len(sourced)
    print(f"\nSourced {len(resolved)}/{len(sourced)} HVAC Brisbane companies "
          f"anchored to the ABN spine ({rate:.0%} resolution).")
