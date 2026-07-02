"""Offline unit tests for the scrape connectors (plan §5.3) — mocked Apify client.

The Google Maps fixture (``tests/fixtures/maps_place.json``) is a real actor item
captured by ``scripts/apify_smoke_test.py`` so the offline test matches live keys
exactly (plan guardrail).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sourcing.connectors.cache import InMemoryTTLCache
from sourcing.connectors.google_maps import GoogleMapsConnector
from sourcing.connectors.linkedin import LinkedInHeadcountConnector
from sourcing.connectors.website import WebsiteFetchConnector
from sourcing.connectors.yellow_pages import YellowPagesConnector

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "maps_place.json"
MAPS_FIXTURE_PLACE = json.loads(_FIXTURE.read_text())


class FakeApifyClient:
    """Mimics the apify-client 3.x surface (Run object with default_dataset_id)."""

    def __init__(self, items):
        self._items = items
        self.call_count = 0

    def actor(self, actor_id):
        client = self

        class _Actor:
            def call(self, run_input, **kwargs):
                client.call_count += 1

                class _Run:  # mimic the 3.x Run model attribute
                    default_dataset_id = "ds1"

                return _Run()

        return _Actor()

    def dataset(self, dataset_id):
        client = self

        class _DS:
            def list_items(self):
                class _R:
                    items = client._items

                return _R()

        return _DS()


# ---------------------------------------------------------------------------
# Google Maps
# ---------------------------------------------------------------------------

def test_maps_build_input():
    inp = GoogleMapsConnector().build_input(
        {"search_terms": ["HVAC"], "location": "Brisbane QLD", "max_places": 50}
    )
    assert inp["countryCode"] == "au"
    assert inp["searchStringsArray"] == ["HVAC"]
    assert inp["maxCrawledPlacesPerSearch"] == 50


def test_maps_normalize_from_real_fixture():
    rec = GoogleMapsConnector().normalize(MAPS_FIXTURE_PLACE)
    assert rec.legal_name == "Energy Evolution"
    assert rec.sector.category_text            # category present
    assert rec.location.lat is not None        # geo present
    assert rec.contacts_min.get("website")     # website present
    assert rec.abn is None                     # scrape layer never has an ABN
    assert rec.entity_id.startswith("maps:")


def test_maps_normalize_extracts_structured_state_postcode():
    place = dict(MAPS_FIXTURE_PLACE, state="QLD", postalCode="4006", city="Fortitude Valley")
    rec = GoogleMapsConnector().normalize(place)
    assert rec.location.state == "QLD"
    assert rec.location.postcode == "4006"
    assert rec.location.suburb == "Fortitude Valley"


def test_maps_normalize_parses_state_postcode_from_address():
    place = {"title": "X", "address": "12 Wickham St, Brisbane QLD 4000, Australia",
             "categories": ["Plumber"], "location": {"lat": -27.4, "lng": 153.0}}
    rec = GoogleMapsConnector().normalize(place)
    assert rec.location.state == "QLD"
    assert rec.location.postcode == "4000"


def test_maps_cache_hit_skips_second_actor_run():
    fake = FakeApifyClient(items=[MAPS_FIXTURE_PLACE])
    c = GoogleMapsConnector(cache=InMemoryTTLCache(), client=fake)
    params = {"search_terms": ["HVAC"], "location": "Brisbane QLD", "max_places": 3}
    first = c.fetch(params)
    second = c.fetch(params)
    assert first == second
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# Yellow Pages
# ---------------------------------------------------------------------------

def test_yellow_pages_normalize():
    raw = {"name": "Acme Plumbing", "category": "Plumber", "phone": "07 1234 5678",
           "website": "http://acme.com.au", "address": "5 Main St, Brisbane QLD 4000"}
    rec = YellowPagesConnector().normalize(raw)
    assert rec.legal_name == "Acme Plumbing"
    assert rec.sector.category_text == ["Plumber"]
    assert rec.location.state == "QLD" and rec.location.postcode == "4000"
    assert rec.contacts_min["website"] == "http://acme.com.au"
    assert rec.abn is None


# ---------------------------------------------------------------------------
# Website fetch
# ---------------------------------------------------------------------------

def test_website_attaches_text():
    raw = {"metadata": {"url": "https://acme.com.au", "title": "Acme"},
           "markdown": "# Acme\nWe do testing and certification."}
    rec = WebsiteFetchConnector().normalize(raw)
    assert rec.entity_id == "web:https://acme.com.au"
    assert "testing and certification" in rec.deferred_assessment["website_text_raw"]
    assert rec.abn is None


# ---------------------------------------------------------------------------
# LinkedIn (shortlist-gated)
# ---------------------------------------------------------------------------

def test_linkedin_normalize_headcount():
    rec = LinkedInHeadcountConnector().normalize({"companyName": "Acme", "employeeCount": 42})
    assert rec.size.employee_count == 42
    assert rec.size.employee_source == "linkedin"


def test_linkedin_is_shortlist_gated():
    assert LinkedInHeadcountConnector.gate == "shortlist_only"


@pytest.mark.parametrize("conn", [GoogleMapsConnector, YellowPagesConnector,
                                  WebsiteFetchConnector, LinkedInHeadcountConnector])
def test_scrape_connectors_have_actor_and_source_ids(conn):
    assert conn.actor_id and conn.source_id
