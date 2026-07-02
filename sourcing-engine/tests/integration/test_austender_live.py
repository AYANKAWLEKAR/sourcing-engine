"""Live integration test for AusTenderConnector (addendum §6.2, reconciled).

The real OCDS API has no per-ABN endpoint, so this proves: (1) the date-window
scan against the live API works, and (2) a supplier found in that window is
looked up and normalized correctly. No credentials required.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.austender import AusTenderConnector, _supplier_abn
from sourcing.connectors.cache import InMemoryTTLCache
from sourcing.models.company import CompanyRecord

pytestmark = pytest.mark.integration

# A short, historical window known to contain published contracts.
_START = "2024-06-03T00:00:00Z"
_END = "2024-06-05T00:00:00Z"


@pytest.fixture(scope="module")
def connector():
    return AusTenderConnector(cache=InMemoryTTLCache())


def test_window_scan_returns_releases(connector):
    releases = connector._scan_window(_START, _END)
    assert len(releases) > 5
    assert all("ocid" in r for r in releases)


def test_supplier_lookup_and_normalize(connector):
    releases = connector._scan_window(_START, _END)
    # Find a supplier ABN present in the window, then look it up.
    abn = next((_supplier_abn(r) for r in releases if _supplier_abn(r)), None)
    assert abn, "expected at least one supplier ABN in the window"

    matched = connector.fetch({"abn": abn, "start_date": _START, "end_date": _END})
    assert matched, f"supplier {abn} should be found in its own window"

    rec = connector.normalize(matched[0])
    assert rec.moat_signals.gov_contracts is True
    assert rec.abn == abn


def test_enrich_record_end_to_end(connector):
    releases = connector._scan_window(_START, _END)
    abn = next((_supplier_abn(r) for r in releases if _supplier_abn(r)), None)
    rec = CompanyRecord(abn=abn, legal_name="Live supplier")
    connector.enrich_record(rec, window={"start_date": _START, "end_date": _END})
    assert rec.moat_signals.gov_contracts is True
    assert rec.moat_signals.gov_contract_count and rec.moat_signals.gov_contract_count >= 1
