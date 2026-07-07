"""Live ASXListedConnector integration — real CSV from data/, temp DuckDB."""
from __future__ import annotations

import pytest

from sourcing.connectors.asx_listed import ASXListedConnector
from sourcing.models.company import CompanyRecord
from sourcing.rank.buybox import BuyBox
from sourcing.rank.screen import screen

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def connector(require_asx_csv, tmp_path_factory):
    db = tmp_path_factory.mktemp("asx") / "bulk.duckdb"
    c = ASXListedConnector(db_path=db, csv_path=require_asx_csv)
    c.ensure_loaded()
    yield c
    c.close()


def test_row_count_in_expected_range(connector):
    # ~1800–2400 companies are listed on the ASX at any given time.
    assert 1000 < connector.row_count() < 4000


def test_known_listing_matches_by_name(connector):
    rec = CompanyRecord(entity_id="x", legal_name="1414 Degrees Limited")
    assert connector.is_listed(rec) == (True, 0.75)


def test_private_company_misses(connector):
    rec = CompanyRecord(entity_id="x", legal_name="Joe's Brisbane Plumbing Pty Ltd")
    assert connector.is_listed(rec) == (False, 0.0)


def test_enrich_and_screen_excludes_listed(connector):
    rec = CompanyRecord(entity_id="x", legal_name="1414 Degrees Limited")
    connector.enrich_record(rec)
    assert rec.ownership.listed_entity is True
    assert screen(rec, BuyBox(exclude_listed=True)) is False
    assert "exclude:listed_entity" in rec.screen.flags


def test_normalized_names_populated(connector):
    empties = connector.query(
        "SELECT count(*) AS n FROM asx_listed WHERE normalized_name = '' OR normalized_name IS NULL"
    )[0]["n"]
    assert empties == 0
