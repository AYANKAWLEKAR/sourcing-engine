"""Live IPGODConnector integration — real applicant CSV(s), temp DuckDB."""
from __future__ import annotations

from datetime import date

import pytest

from sourcing.connectors.ipgod import IPGODConnector
from sourcing.models.company import CompanyRecord

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def connector(require_ipgod_csvs, tmp_path_factory):
    db = tmp_path_factory.mktemp("ipgod") / "bulk.duckdb"
    c = IPGODConnector(db_path=db, csv_paths=require_ipgod_csvs)
    c.ensure_loaded()
    yield c
    c.close()


def test_aggregated_rows_substantial(connector):
    # Applicant files carry tens of thousands of distinct ABNs.
    assert connector.row_count() > 10_000


def test_all_abns_are_11_digits(connector):
    bad = connector.query(
        "SELECT count(*) AS n FROM ipgod "
        "WHERE length(abn) != 11 OR regexp_matches(abn, '[^0-9]')"
    )[0]["n"]
    assert bad == 0


def test_earliest_years_sane(connector):
    row = connector.query(
        "SELECT min(earliest_year) AS lo, max(earliest_year) AS hi FROM ipgod "
        "WHERE earliest_year IS NOT NULL"
    )[0]
    assert row["lo"] >= 1900
    assert row["hi"] <= date.today().year


def test_known_applicant_round_trip(connector):
    # Pick a real applicant at runtime and round-trip it through the full path.
    sample = connector.query("SELECT abn FROM ipgod LIMIT 1")[0]["abn"]
    assert connector.has_ip(sample) is True

    rec = CompanyRecord(entity_id="x", abn=sample)
    connector.enrich_record(rec)
    assert rec.moat_signals.ip is True
    assert rec.moat_signals.ip_count >= 1
    assert rec.moat_signals.ip_types
    assert any(p.source == "ipgod" for p in rec.provenance)


def test_miss_leaves_ip_unknown(connector):
    rec = CompanyRecord(entity_id="x", abn="00000000000")
    connector.enrich_record(rec)
    assert rec.moat_signals.ip is None
    assert "ipgod_checked_no_ip" in rec.flags
