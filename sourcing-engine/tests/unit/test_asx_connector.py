"""ASXListedConnector unit tests — synthetic CSVs, temp DuckDB, no network.

Covers: defensive header resolution (with and without an ACN column), name
normalization + matching thresholds, enrich_record semantics (set-on-match only,
never False), and the end-to-end screen wiring (`exclude:listed_entity`).
"""
from __future__ import annotations

import pytest

from sourcing.connectors.asx_listed import (
    ACN_CONFIDENCE,
    NAME_CONFIDENCE,
    ASXListedConnector,
    _norm_name,
)
from sourcing.models.company import CompanyRecord
from sourcing.rank.buybox import BuyBox
from sourcing.rank.screen import screen

# The real export's headers (no ACN column).
_REAL_CSV = '''"ASX code","Company name","GICs industry group","Listing date","Market Cap"
"ACM","ACME WIDGETS LIMITED","Capital Goods","01/01/2010",1000000
"EXH","EXAMPLE HOLDINGS LTD","Materials","02/02/2012",2000000
"CAF","Café Delight Limited","Food & Beverage","03/03/2015",3000000
'''

# A hypothetical future export that adds ACN (reordered headers on purpose).
_ACN_CSV = '''"Company name","ACN","ASX code","Market Cap"
"ACME WIDGETS LIMITED","004 000 001","ACM",1000000
"EXAMPLE HOLDINGS LTD","004000002","EXH",2000000
'''

_GARBAGE_CSV = '''"foo","bar"
"1","2"
'''


def _connector(tmp_path, csv_text: str, filename: str = "asx.csv") -> ASXListedConnector:
    csv_path = tmp_path / filename
    csv_path.write_text(csv_text, encoding="utf-8")
    c = ASXListedConnector(db_path=tmp_path / "bulk.duckdb", csv_path=str(csv_path))
    c.ensure_loaded()
    return c


@pytest.fixture
def connector(tmp_path):
    c = _connector(tmp_path, _REAL_CSV)
    yield c
    c.close()


@pytest.fixture
def acn_connector(tmp_path):
    c = _connector(tmp_path, _ACN_CSV)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

class TestNormName:
    def test_strips_legal_suffixes_and_case(self):
        assert _norm_name("Example Holdings Pty Ltd") == "example holdings"
        assert _norm_name("EXAMPLE HOLDINGS LTD") == "example holdings"
        assert _norm_name("Acme Widgets Limited") == "acme widgets"

    def test_keeps_descriptive_words(self):
        # "holdings"/"group" must survive — stripping them would collapse
        # distinct companies onto the same key.
        assert "holdings" in _norm_name("Example Holdings Ltd")

    def test_empty_and_none(self):
        assert _norm_name(None) == ""
        assert _norm_name("") == ""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class TestLoad:
    def test_loads_all_rows(self, connector):
        assert connector.row_count() == 3

    def test_no_acn_column_yields_null_acn(self, connector):
        rows = connector.query("SELECT acn FROM asx_listed")
        assert all(r["acn"] is None for r in rows)

    def test_acn_variant_normalizes_digits(self, acn_connector):
        rows = acn_connector.query("SELECT acn FROM asx_listed WHERE asx_code = 'ACM'")
        assert rows[0]["acn"] == "004000001"  # spaces stripped, zeros kept

    def test_garbage_headers_raise(self, tmp_path):
        csv_path = tmp_path / "garbage.csv"
        csv_path.write_text(_GARBAGE_CSV, encoding="utf-8")
        c = ASXListedConnector(db_path=tmp_path / "bulk.duckdb", csv_path=str(csv_path))
        with pytest.raises(ValueError, match="headers not recognised"):
            c.ensure_loaded()
        c.close()

    def test_missing_csv_raises_file_not_found(self, tmp_path):
        c = ASXListedConnector(db_path=tmp_path / "bulk.duckdb", csv_path=str(tmp_path / "nope.csv"))
        with pytest.raises(FileNotFoundError):
            c.ensure_loaded()
        c.close()


# ---------------------------------------------------------------------------
# is_listed
# ---------------------------------------------------------------------------

class TestIsListed:
    def test_name_match_ignores_suffix_and_case(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Example Holdings Pty Ltd")
        assert connector.is_listed(rec) == (True, NAME_CONFIDENCE)

    def test_trading_name_match(self, connector):
        rec = CompanyRecord(
            entity_id="x", legal_name="Zed Pty Ltd", trading_names=["Acme Widgets"]
        )
        assert connector.is_listed(rec) == (True, NAME_CONFIDENCE)

    def test_accented_name_match(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Caf Delight")
        # Accented chars are dropped by normalization on both sides.
        assert connector.is_listed(rec)[0] is True

    def test_non_listed_misses(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Brisbane Plumbing Pty Ltd")
        assert connector.is_listed(rec) == (False, 0.0)

    def test_acn_match_beats_name(self, acn_connector):
        rec = CompanyRecord(entity_id="x", acn="004000001", legal_name="Totally Different")
        assert acn_connector.is_listed(rec) == (True, ACN_CONFIDENCE)


# ---------------------------------------------------------------------------
# enrich_record
# ---------------------------------------------------------------------------

class TestEnrichRecord:
    def test_match_sets_listed_true_with_provenance(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Acme Widgets Ltd")
        connector.enrich_record(rec)
        assert rec.ownership.listed_entity is True
        prov = [p for p in rec.provenance if p.field == "ownership.listed_entity"]
        assert len(prov) == 1
        assert prov[0].source == "asx_listed_list"
        assert prov[0].confidence == NAME_CONFIDENCE
        assert "asx_code=ACM" in (prov[0].locator or "")
        assert "asx_name_match_only" in rec.flags

    def test_no_match_leaves_none_never_false(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Brisbane Plumbing Pty Ltd")
        connector.enrich_record(rec)
        assert rec.ownership.listed_entity is None
        assert rec.provenance == []
        assert rec.flags == []

    def test_acn_match_has_no_name_only_flag(self, acn_connector):
        rec = CompanyRecord(entity_id="x", acn="004000002", legal_name="Whatever")
        acn_connector.enrich_record(rec)
        assert rec.ownership.listed_entity is True
        assert "asx_name_match_only" not in rec.flags


# ---------------------------------------------------------------------------
# fetch / normalize contract
# ---------------------------------------------------------------------------

class TestFetchNormalize:
    def test_fetch_by_name(self, connector):
        recs = connector.fetch({"name": "Acme Widgets Pty Ltd"})
        assert len(recs) == 1
        assert recs[0]["org_name"] == "ACME WIDGETS LIMITED"

    def test_fetch_all_respects_limit(self, connector):
        assert len(connector.fetch({"limit": 2})) == 2

    def test_normalize_sets_listed_entity(self, connector):
        raw = connector.fetch({"name": "Acme Widgets"})[0]
        company = connector.normalize(raw)
        assert company.entity_id == "asx:ACM"
        assert company.legal_name == "ACME WIDGETS LIMITED"
        assert company.ownership.listed_entity is True
        assert all(p.source == "asx_listed_list" for p in company.provenance)


# ---------------------------------------------------------------------------
# Screen wiring — the point of the connector
# ---------------------------------------------------------------------------

class TestScreenWiring:
    def test_enriched_listed_record_is_excluded(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Example Holdings Ltd")
        connector.enrich_record(rec)
        assert screen(rec, BuyBox(exclude_listed=True)) is False
        assert rec.screen.status == "excluded"
        assert "exclude:listed_entity" in rec.screen.flags

    def test_unmatched_record_survives(self, connector):
        rec = CompanyRecord(entity_id="x", legal_name="Brisbane Plumbing Pty Ltd")
        connector.enrich_record(rec)
        assert screen(rec, BuyBox(exclude_listed=True)) is True
