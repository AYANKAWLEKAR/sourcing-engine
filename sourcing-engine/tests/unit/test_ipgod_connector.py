"""IPGODConnector unit tests — synthetic applicant CSVs, temp DuckDB, no network.

Covers: per-file ip_type inference, defensive ABN/year column detection (year
column vs date column), aggregation to (abn, ip_type), leading-zero ABNs,
invalid-ABN drops, and enrich_record set-on-match semantics.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.ipgod import IPGODConnector, _infer_ip_type
from sourcing.models.company import CompanyRecord

# Patents CSV: plain "ABN" column + an application-year column.
# 01111111111 keeps its leading zero; 22222222222 has 3 rows across 2 years;
# one blank-ABN row and one malformed-ABN row must be dropped.
_PATENTS_CSV = """Australian Appl No,ABN,Application Year,Applicant Name
P001,01111111111,2015,Alpha Pty Ltd
P002,22222222222,2019,Beta Pty Ltd
P003,22222222222,2012,Beta Pty Ltd
P004,22222222222,2020,Beta Pty Ltd
P005,,2018,No Abn Co
P006,123,2018,Short Abn Co
"""

# Trade marks CSV: variant header ("applicant_abn") + a date column, no year.
_TRADEMARKS_CSV = """tm_number,applicant_abn,filing_date,owner
T001,22222222222,2008-05-01,Beta Pty Ltd
T002,33 333 333 333,2021-09-15,Gamma Pty Ltd
"""


@pytest.fixture
def connector(tmp_path):
    patents = tmp_path / "ipgod_patents_2025.csv"
    patents.write_text(_PATENTS_CSV, encoding="utf-8")
    trademarks = tmp_path / "ipgod_trade_marks_2025.csv"
    trademarks.write_text(_TRADEMARKS_CSV, encoding="utf-8")
    c = IPGODConnector(
        db_path=tmp_path / "bulk.duckdb",
        csv_paths=[str(patents), str(trademarks)],
    )
    c.ensure_loaded()
    yield c
    c.close()


class TestIpTypeInference:
    def test_filename_hints(self):
        assert _infer_ip_type("ipgod_patents_2025.csv") == "patent"
        assert _infer_ip_type("IPGOD_Trade_Marks.csv") == "trademark"
        assert _infer_ip_type("designs_applicants.csv") == "design"
        assert _infer_ip_type("plant_breeders.csv") == "plant_breeder"
        assert _infer_ip_type("mystery.csv") == "unknown"

    def test_explicit_dict_overrides_filename(self, tmp_path):
        p = tmp_path / "mystery.csv"
        p.write_text(_PATENTS_CSV, encoding="utf-8")
        c = IPGODConnector(db_path=tmp_path / "bulk.duckdb", csv_paths={str(p): "patent"})
        c.ensure_loaded()
        rows = c.query("SELECT DISTINCT ip_type FROM ipgod")
        assert [r["ip_type"] for r in rows] == ["patent"]
        c.close()


class TestLoad:
    def test_aggregates_one_row_per_abn_and_type(self, connector):
        rows = connector.query(
            "SELECT * FROM ipgod WHERE abn = '22222222222' ORDER BY ip_type"
        )
        assert len(rows) == 2  # patent + trademark
        patent = next(r for r in rows if r["ip_type"] == "patent")
        assert patent["ip_count"] == 3
        assert patent["earliest_year"] == 2012

    def test_year_from_date_column(self, connector):
        rows = connector.query("SELECT * FROM ipgod WHERE ip_type = 'trademark' AND abn = '22222222222'")
        assert rows[0]["earliest_year"] == 2008

    def test_preserves_leading_zero_abn(self, connector):
        rows = connector.query("SELECT * FROM ipgod WHERE abn = '01111111111'")
        assert len(rows) == 1

    def test_normalizes_spaced_abn(self, connector):
        rows = connector.query("SELECT * FROM ipgod WHERE abn = '33333333333'")
        assert len(rows) == 1

    def test_drops_blank_and_malformed_abns(self, connector):
        abns = {r["abn"] for r in connector.query("SELECT abn FROM ipgod")}
        assert "" not in abns
        assert "123" not in abns

    def test_missing_abn_column_raises(self, tmp_path):
        bad = tmp_path / "ipgod_patents.csv"
        bad.write_text("id,name\n1,X\n", encoding="utf-8")
        c = IPGODConnector(db_path=tmp_path / "bulk.duckdb", csv_paths=[str(bad)])
        with pytest.raises(ValueError, match="no ABN column"):
            c.ensure_loaded()
        c.close()

    def test_no_paths_configured_raises(self, tmp_path):
        c = IPGODConnector(db_path=tmp_path / "bulk.duckdb", csv_paths=[])
        with pytest.raises(FileNotFoundError, match="IPGOD_CSV_PATHS"):
            c.ensure_loaded()
        c.close()


class TestLookups:
    def test_has_ip(self, connector):
        assert connector.has_ip("22222222222") is True
        assert connector.has_ip("22 222 222 222") is True  # digits-normalized
        assert connector.has_ip("99999999999") is False

    def test_fetch_by_abn(self, connector):
        recs = connector.fetch({"abn": "22222222222"})
        assert [r["raw"]["ip_type"] for r in recs] == ["patent", "trademark"]

    def test_normalize_fragment(self, connector):
        raw = connector.fetch({"abn": "01111111111"})[0]
        company = connector.normalize(raw)
        assert company.abn == "01111111111"
        assert company.moat_signals.ip is True
        assert company.moat_signals.ip_count == 1
        assert company.moat_signals.ip_types == ["patent"]
        assert company.provenance[0].source == "ipgod"
        assert company.provenance[0].confidence == 0.9


class TestEnrichRecord:
    def test_match_merges_counts_across_types(self, connector):
        rec = CompanyRecord(entity_id="x", abn="22222222222")
        connector.enrich_record(rec)
        assert rec.moat_signals.ip is True
        assert rec.moat_signals.ip_count == 4  # 3 patents + 1 trademark
        assert rec.moat_signals.ip_types == ["patent", "trademark"]
        prov = [p for p in rec.provenance if p.field == "moat_signals.ip"]
        assert len(prov) == 1
        assert prov[0].source == "ipgod"

    def test_miss_leaves_ip_none_with_flag(self, connector):
        rec = CompanyRecord(entity_id="x", abn="99999999999")
        connector.enrich_record(rec)
        assert rec.moat_signals.ip is None
        assert rec.moat_signals.ip_count is None
        assert "ipgod_checked_no_ip" in rec.flags

    def test_no_abn_is_noop(self, connector):
        rec = CompanyRecord(entity_id="x")
        connector.enrich_record(rec)
        assert rec.moat_signals.ip is None
        assert rec.flags == []


# ---------------------------------------------------------------------------
# IPGOD2022 party-activity shape (float ABNs, roles, repeated app rows)
# ---------------------------------------------------------------------------

_PARTY_ACTIVITY_CSV = """ip_right_type,application_number,party_role_category,party_name,abn,effective_from_date
patent,2009211661,applicant,acme labs pty ltd,44444444444.0,2010-08-17 00:00:00.000
patent,2009211661,applicant,acme labs pty ltd,44444444444.0,2015-01-02 00:00:00.000
patent,2011000001,applicant,acme labs pty ltd,44444444444.0,2011-03-04 00:00:00.000
patent,2009211661,applicant_agent,slick attorneys pty ltd,55555555555.0,2010-08-17 00:00:00.000
patent,2012327835,mortgagee,big bank limited,66666666666.0,2020-10-15 00:00:00.000
"""


@pytest.fixture
def party_connector(tmp_path):
    p = tmp_path / "patent-party-activity.csv"
    p.write_text(_PARTY_ACTIVITY_CSV, encoding="utf-8")
    c = IPGODConnector(db_path=tmp_path / "bulk.duckdb", csv_paths=[str(p)])
    c.ensure_loaded()
    yield c
    c.close()


class TestPartyActivityShape:
    def test_float_formatted_abns_cleaned(self, party_connector):
        assert party_connector.has_ip("44444444444") is True

    def test_counts_distinct_applications_not_rows(self, party_connector):
        # 3 applicant rows but only 2 distinct applications.
        rows = party_connector.query("SELECT * FROM ipgod WHERE abn = '44444444444'")
        assert rows[0]["ip_count"] == 2

    def test_agents_and_mortgagees_excluded(self, party_connector):
        assert party_connector.has_ip("55555555555") is False  # applicant_agent
        assert party_connector.has_ip("66666666666") is False  # mortgagee

    def test_earliest_year_from_timestamp(self, party_connector):
        rows = party_connector.query("SELECT * FROM ipgod WHERE abn = '44444444444'")
        assert rows[0]["earliest_year"] == 2010
