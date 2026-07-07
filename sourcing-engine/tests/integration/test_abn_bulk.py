"""Live ABNBulkExtractConnector integration — real ABR zips, temp DuckDB.

The load tests are gated on pre-downloaded zips (``require_abn_bulk_zips``);
they never download. The CKAN-resolution test is separately opt-in via
``ABN_BULK_ALLOW_DOWNLOAD=1`` and only resolves URLs (no 1.7GB fetch).
"""
from __future__ import annotations

import os

import pytest

from sourcing.connectors.abn_bulk import (
    CKAN_PACKAGE_URL,
    ZIP_NAMES,
    ABNBulkExtractConnector,
)

pytestmark = pytest.mark.integration

# The ATO — a permanent, well-known ABN; stable for assertions.
_ATO_ABN = "51824753556"


@pytest.fixture(scope="module")
def connector(require_abn_bulk_zips, tmp_path_factory):
    db = tmp_path_factory.mktemp("abn_bulk") / "bulk.duckdb"
    c = ABNBulkExtractConnector(
        db_path=db, zip_paths=require_abn_bulk_zips, allow_download=False
    )
    c.ensure_loaded()  # minutes-long on the full extract
    yield c
    c.close()


def test_row_count_covers_the_register(connector, require_abn_bulk_zips):
    n = connector.row_count()
    # Full register is ~10M+ ABNs across both zips; a single zip still carries
    # millions. Guard against a truncated/partial load either way.
    expected_floor = 5_000_000 if len(require_abn_bulk_zips) >= 2 else 2_000_000
    assert n > expected_floor


def test_ato_point_lookup(connector):
    rec = connector.lookup_abn(_ATO_ABN)
    assert rec is not None
    assert rec["abn"] == _ATO_ABN
    assert "TAXATION" in (rec["org_name"] or "").upper()


def test_geography_mostly_populated(connector):
    cov = connector.query(
        "SELECT count(*) AS total, count(state) AS with_state FROM abn_extract "
        "WHERE abn_status = 'ACT'"
    )[0]
    assert cov["with_state"] / cov["total"] > 0.5


def test_acns_preserve_leading_zeros(connector):
    rows = connector.query(
        "SELECT acn FROM abn_extract WHERE acn LIKE '0%' LIMIT 5"
    )
    assert rows, "expected some leading-zero ACNs in the register"
    assert all(len(r["acn"]) == 9 for r in rows)


def test_fetch_active_sole_traders(connector):
    recs = connector.fetch({"entity_types": ["IND"], "min_years": 10, "limit": 50})
    assert recs
    assert all(r["entity_type_code"] == "IND" for r in recs)
    assert all(r["status_code"] == "ACT" for r in recs)


def test_normalize_live_row(connector):
    company = connector.normalize(connector.lookup_abn(_ATO_ABN))
    assert company.abn == _ATO_ABN
    assert company.legal_name
    assert all(p.source == "abn_bulk_extract" for p in company.provenance)


@pytest.mark.skipif(
    not os.environ.get("ABN_BULK_ALLOW_DOWNLOAD"),
    reason="opt-in: set ABN_BULK_ALLOW_DOWNLOAD=1 to hit the live CKAN API",
)
def test_ckan_package_resolves_both_zips():
    """CKAN package_show must expose both split-zip resources (URL check only)."""
    import httpx

    resp = httpx.get(CKAN_PACKAGE_URL, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    resources = (resp.json().get("result") or {}).get("resources") or []
    basenames = {(r.get("url") or "").rsplit("/", 1)[-1].lower() for r in resources}
    for name in ZIP_NAMES:
        assert name in basenames, f"CKAN package missing {name}"
