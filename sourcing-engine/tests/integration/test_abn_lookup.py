"""Integration tests for the ABN Lookup API connector (plan §6) — needs a live GUID.

Hits the real ABR JSONP endpoints. Skipped automatically when ABN_LOOKUP_GUID is
not set. Uses an extremely stable public record: ABN 51824753556 (the Australian
Taxation Office).
"""
from __future__ import annotations

import pytest

from sourcing.connectors.abn import ABNLookupAPIConnector

pytestmark = pytest.mark.integration

# The ATO — a permanent, well-known ABN; stable for assertions.
_ATO_ABN = "51824753556"
_ATO_NAME_FRAGMENT = "AUSTRALIAN TAXATION OFFICE"


@pytest.fixture(scope="module")
def connector(require_abn_guid):
    # Fresh in-memory cache per module so we exercise the live endpoint.
    from sourcing.connectors.cache import InMemoryTTLCache

    return ABNLookupAPIConnector(guid=require_abn_guid, cache=InMemoryTTLCache())


class TestLiveDetail:
    def test_detail_returns_record(self, connector):
        records = connector.fetch({"abn": _ATO_ABN})
        assert len(records) == 1

    def test_detail_legal_name(self, connector):
        records = connector.fetch({"abn": _ATO_ABN})
        company = connector.normalize(records[0])
        assert _ATO_NAME_FRAGMENT in (company.legal_name or "").upper()

    def test_detail_has_state_and_years(self, connector):
        records = connector.fetch({"abn": _ATO_ABN})
        company = connector.normalize(records[0])
        assert company.location.state
        assert company.age.years_operating is not None and company.age.years_operating > 0

    def test_detail_entity_id(self, connector):
        records = connector.fetch({"abn": _ATO_ABN})
        company = connector.normalize(records[0])
        assert company.entity_id == f"abn:{_ATO_ABN}"


class TestLiveNameMatch:
    def test_name_match_finds_known_abn(self, connector):
        records = connector.fetch({"name": "Australian Taxation Office"})
        abns = {r["abn"] for r in records}
        assert _ATO_ABN in abns

    def test_name_match_candidates_have_scores(self, connector):
        records = connector.fetch({"name": "Australian Taxation Office"})
        assert records
        assert all("Score" in r["raw"] for r in records)
