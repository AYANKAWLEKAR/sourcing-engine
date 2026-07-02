"""Integration test for ASICBulkConnector (plan §6) — needs the local ASIC CSV.

Loads the real ~4.4M-row extract into a temp DuckDB and verifies the spine:
row count in range, leading-zero ACNs, the ACN→ABN bridge coverage, point
lookups, a filtered slice, and normalisation. Skipped when ASIC_CSV_PATH is unset.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.asic_bulk import ASICBulkConnector

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def connector(require_asic_csv, tmp_path_factory):
    db = tmp_path_factory.mktemp("asic") / "bulk.duckdb"
    c = ASICBulkConnector(db_path=db, csv_path=require_asic_csv)
    c.ensure_loaded()
    yield c
    c.close()


def test_row_count_in_expected_range(connector):
    n = connector.row_count()
    # The national company register is in the millions; guard against a
    # truncated/partial load.
    assert n > 1_000_000


def test_acn_to_abn_bridge_has_coverage(connector):
    cov = connector.query(
        "SELECT count(*) AS total, count(abn) AS with_abn FROM asic_companies"
    )[0]
    assert cov["with_abn"] > 0
    # ABN should be present on the large majority of rows (the resolver bridge).
    assert cov["with_abn"] / cov["total"] > 0.5


def test_point_lookup_preserves_leading_zeros(connector):
    rec = connector.lookup_acn("000000019")
    assert rec is not None
    assert rec["acn"] == "000000019"
    assert rec["abn"]  # has a linked ABN


def test_lookup_by_abn_roundtrips(connector):
    rec = connector.lookup_acn("000000019")
    abn = rec["abn"]
    back = connector.lookup_abn(abn)
    assert back is not None
    assert back["acn"] == "000000019"


def test_fetch_slice_is_deduped_and_active(connector):
    rows = connector.fetch({"entity_types": ["APTY"], "min_years": 20, "limit": 50})
    assert rows
    acns = [r["acn"] for r in rows]
    assert len(acns) == len(set(acns))  # no duplicate ACNs
    assert all(r["status_code"] == "REGD" for r in rows)


def test_normalize_live_row(connector):
    rec = connector.lookup_acn("000000019")
    company = connector.normalize(rec)
    assert company.acn == "000000019"
    assert company.abn
    assert company.legal_name
    assert company.age.years_operating is not None
    assert all(p.source == "asic_company_dataset" for p in company.provenance)
