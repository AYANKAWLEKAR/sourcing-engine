"""Unit tests for ASICBulkConnector (plan §5 bulk connectors).

Uses a tiny tab-delimited, UTF-8-with-BOM fixture that mirrors the real ASIC
extract's shape (15 columns, leading-zero ACNs, an accented name, a renamed
company with two rows, a deregistered company). Loads into a temp DuckDB.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.asic_bulk import ASICBulkConnector

_HEADER = [
    "Company Name", "ACN", "Type", "Class", "Sub Class", "Status",
    "Date of Registration", "Date of Deregistration",
    "Previous State of Registration", "State Registration number",
    "Modified since last report", "Current Name Indicator",
    "ABN", "Current Name", "Current Name Start Date",
]

_ROWS = [
    # current company, leading-zero ACN
    ["ACME WIDGETS PTY LTD", "004000001", "APTY", "LMSH", "PROP", "REGD",
     "01/03/2000", "", "VIC", "123", "", "Y", "11004000001", "", ""],
    # accented name — must not corrupt
    ["Café Delight Pty Ltd", "004000002", "APTY", "LMSH", "PROP", "REGD",
     "15/06/2015", "", "NSW", "456", "", "Y", "22004000002", "", ""],
    # renamed company: former-name row (no Y) ...
    ["OLD NAME PTY LTD", "004000003", "APTY", "LMSH", "PROP", "REGD",
     "10/10/1995", "", "QLD", "789", "", "", "33004000003", "NEW NAME PTY LTD", "01/01/2010"],
    # ... and the canonical current-name row (Y)
    ["NEW NAME PTY LTD", "004000003", "APTY", "LMSH", "PROP", "REGD",
     "10/10/1995", "", "QLD", "789", "", "Y", "33004000003", "", ""],
    # deregistered — excluded by default status filter
    ["DEREGISTERED CO PTY LTD", "004000004", "APTY", "LMSH", "PROP", "DRGD",
     "05/05/1990", "01/01/2005", "WA", "999", "", "Y", "44004000004", "", ""],
    # public company
    ["PUBLIC CO LTD", "004000005", "APUB", "LMSH", "", "REGD",
     "20/02/2018", "", "NSW", "111", "", "Y", "55004000005", "", ""],
]


@pytest.fixture
def connector(tmp_path):
    csv_path = tmp_path / "asic_fixture.csv"
    lines = ["\t".join(_HEADER)] + ["\t".join(r) for r in _ROWS]
    # utf-8-sig writes the BOM, matching the real file.
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    c = ASICBulkConnector(db_path=tmp_path / "bulk.duckdb", csv_path=str(csv_path))
    c.ensure_loaded()
    yield c
    c.close()


def test_loads_all_rows(connector):
    assert connector.row_count() == len(_ROWS)


def test_preserves_leading_zeros(connector):
    rec = connector.lookup_acn("004000001")
    assert rec is not None
    assert rec["acn"] == "004000001"  # not 4000001
    assert rec["abn"] == "11004000001"


def test_no_unicode_corruption(connector):
    rec = connector.lookup_acn("004000002")
    assert rec is not None
    assert "Café" in rec["org_name"]


def test_lookup_abn(connector):
    rec = connector.lookup_abn("22004000002")
    assert rec is not None
    assert rec["acn"] == "004000002"


def test_renamed_company_returns_canonical_name(connector):
    rec = connector.lookup_acn("004000003")
    assert rec is not None
    assert rec["org_name"] == "NEW NAME PTY LTD"  # the Y row, not OLD NAME


def test_fetch_excludes_deregistered_by_default(connector):
    acns = {r["acn"] for r in connector.fetch({})}
    assert "004000004" not in acns  # DRGD filtered out


def test_fetch_dedupes_renamed_company(connector):
    rows = connector.fetch({})
    acns = [r["acn"] for r in rows]
    assert acns.count("004000003") == 1


def test_fetch_filters_entity_type(connector):
    rows = connector.fetch({"entity_types": ["APUB"]})
    assert {r["acn"] for r in rows} == {"004000005"}


def test_fetch_filters_min_years(connector):
    # 2018 registration → well under 30 years; excluded.
    rows = connector.fetch({"min_years": 30})
    assert "004000005" not in {r["acn"] for r in rows}
    # but included at a low threshold
    rows2 = connector.fetch({"min_years": 3})
    assert "004000005" in {r["acn"] for r in rows2}


def test_normalize_private_company(connector):
    rec = connector.lookup_acn("004000001")
    company = connector.normalize(rec)
    assert company.entity_id == "abn:11004000001"
    assert company.acn == "004000001"
    assert company.abn == "11004000001"
    assert company.legal_name == "ACME WIDGETS PTY LTD"
    assert company.ownership.structure_guess == "private-company"
    assert company.age.abn_registered == "2000-03-01"
    assert company.age.years_operating is not None and company.age.years_operating >= 20


def test_normalize_public_company(connector):
    rec = connector.lookup_acn("004000005")
    company = connector.normalize(rec)
    assert company.ownership.structure_guess == "public-company"


def test_normalize_provenance_is_asic(connector):
    rec = connector.lookup_acn("004000001")
    company = connector.normalize(rec)
    assert all(p.source == "asic_company_dataset" for p in company.provenance)
    assert {"acn", "entity_type"} <= {p.field for p in company.provenance}
