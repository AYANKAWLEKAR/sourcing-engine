"""Live integration test for the Google Maps scrape connector (plan §5.3).

Needs APIFY_API_TOKEN. Runs the real actor on a small HVAC/Brisbane slice.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.google_maps import GoogleMapsConnector

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def maps(require_apify_token):
    from sourcing.connectors.cache import InMemoryTTLCache

    return GoogleMapsConnector(cache=InMemoryTTLCache())


@pytest.fixture(scope="module")
def hvac_brisbane(maps):
    return maps.fetch({
        "search_terms": ["HVAC installer", "air conditioning"],
        "location": "Brisbane QLD Australia",
        "max_places": 50,
    })


def test_maps_returns_at_least_20(hvac_brisbane):
    assert len(hvac_brisbane) >= 20, f"only {len(hvac_brisbane)} places"


def test_maps_records_carry_category(maps, hvac_brisbane):
    recs = [maps.normalize(r) for r in hvac_brisbane]
    with_cat = sum(1 for r in recs if r.sector.category_text)
    assert with_cat / len(recs) > 0.8, f"only {with_cat}/{len(recs)} carry category"


def test_maps_records_have_no_abn(maps, hvac_brisbane):
    recs = [maps.normalize(r) for r in hvac_brisbane]
    assert all(r.abn is None for r in recs)  # scrape layer never produces an ABN


def test_maps_records_have_geo(maps, hvac_brisbane):
    recs = [maps.normalize(r) for r in hvac_brisbane]
    with_geo = sum(1 for r in recs if r.location.lat is not None)
    assert with_geo / len(recs) > 0.8
