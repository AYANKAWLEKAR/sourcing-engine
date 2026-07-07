"""ABNBulkExtractConnector unit tests — synthetic ABR XML zipped in tmp_path.

Covers: zip-streamed iterparse (non-individual vs individual names, trading
names, leading zeros), status/entity-type/min_years/state/limit filters,
ASIC→ABR type-code translation, multi-zip union + duplicate-ABN safety,
normalize provenance, and the no-download-by-default guard.
"""
from __future__ import annotations

import zipfile

import pytest

from sourcing.connectors.abn_bulk import ABNBulkExtractConnector

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Transfer>
  <ABR recordLastUpdatedDate="20250104" replaced="N">
    <ABN status="ACT" ABNStatusFromDate="20000224">11004000001</ABN>
    <EntityType>
      <EntityTypeInd>PRV</EntityTypeInd>
      <EntityTypeText>Australian Private Company</EntityTypeText>
    </EntityType>
    <MainEntity>
      <NonIndividualName type="MN">
        <NonIndividualNameText>ACME WIDGETS PTY LTD</NonIndividualNameText>
      </NonIndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>VIC</State>
          <Postcode>3000</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </MainEntity>
    <ASICNumber ASICNumberType="undetermined">004000001</ASICNumber>
    <OtherEntity>
      <NonIndividualName type="TRD">
        <NonIndividualNameText>Acme Widgets</NonIndividualNameText>
      </NonIndividualName>
    </OtherEntity>
    <OtherEntity>
      <NonIndividualName type="BN">
        <NonIndividualNameText>Widget World</NonIndividualNameText>
      </NonIndividualName>
    </OtherEntity>
  </ABR>
  <ABR recordLastUpdatedDate="20250104" replaced="N">
    <ABN status="ACT" ABNStatusFromDate="19950101">22000000002</ABN>
    <EntityType>
      <EntityTypeInd>IND</EntityTypeInd>
      <EntityTypeText>Individual/Sole Trader</EntityTypeText>
    </EntityType>
    <LegalEntity>
      <IndividualName type="LGL">
        <NameTitle>MR</NameTitle>
        <GivenName>JOHN</GivenName>
        <GivenName>QUINCY</GivenName>
        <FamilyName>SMITH</FamilyName>
      </IndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>QLD</State>
          <Postcode>4000</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </LegalEntity>
  </ABR>
  <ABR recordLastUpdatedDate="20250104" replaced="N">
    <ABN status="CAN" ABNStatusFromDate="20100315">33000000003</ABN>
    <EntityType>
      <EntityTypeInd>PRV</EntityTypeInd>
      <EntityTypeText>Australian Private Company</EntityTypeText>
    </EntityType>
    <MainEntity>
      <NonIndividualName type="MN">
        <NonIndividualNameText>CANCELLED CO PTY LTD</NonIndividualNameText>
      </NonIndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>NSW</State>
          <Postcode>2000</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </MainEntity>
  </ABR>
  <ABR recordLastUpdatedDate="20250104" replaced="N">
    <ABN status="ACT" ABNStatusFromDate="20230601">44000000004</ABN>
    <EntityType>
      <EntityTypeInd>TRT</EntityTypeInd>
      <EntityTypeText>Discretionary Trading Trust</EntityTypeText>
    </EntityType>
    <MainEntity>
      <NonIndividualName type="MN">
        <NonIndividualNameText>THE YOUNG FAMILY TRUST</NonIndividualNameText>
      </NonIndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>QLD</State>
          <Postcode>4101</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </MainEntity>
  </ABR>
</Transfer>
"""

# Second zip: one new record + a duplicate ABN (updated address) of the first.
_XML_2 = """<?xml version="1.0" encoding="UTF-8"?>
<Transfer>
  <ABR recordLastUpdatedDate="20250601" replaced="N">
    <ABN status="ACT" ABNStatusFromDate="20050505">55000000005</ABN>
    <EntityType>
      <EntityTypeInd>PUB</EntityTypeInd>
      <EntityTypeText>Australian Public Company</EntityTypeText>
    </EntityType>
    <MainEntity>
      <NonIndividualName type="MN">
        <NonIndividualNameText>BIG PUBLIC LIMITED</NonIndividualNameText>
      </NonIndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>WA</State>
          <Postcode>6000</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </MainEntity>
  </ABR>
  <ABR recordLastUpdatedDate="20250601" replaced="N">
    <ABN status="ACT" ABNStatusFromDate="20000224">11004000001</ABN>
    <EntityType>
      <EntityTypeInd>PRV</EntityTypeInd>
      <EntityTypeText>Australian Private Company</EntityTypeText>
    </EntityType>
    <MainEntity>
      <NonIndividualName type="MN">
        <NonIndividualNameText>ACME WIDGETS PTY LTD</NonIndividualNameText>
      </NonIndividualName>
      <BusinessAddress>
        <AddressDetails>
          <State>TAS</State>
          <Postcode>7000</Postcode>
        </AddressDetails>
      </BusinessAddress>
    </MainEntity>
    <ASICNumber ASICNumberType="undetermined">004000001</ASICNumber>
  </ABR>
</Transfer>
"""


def _make_zip(path, xml_by_member: dict[str, str]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for member, xml in xml_by_member.items():
            zf.writestr(member, xml)
    return str(path)


@pytest.fixture
def connector(tmp_path):
    zip1 = _make_zip(tmp_path / "public_split_1_10.zip", {"20260101_Public01.xml": _XML})
    c = ABNBulkExtractConnector(
        db_path=tmp_path / "bulk.duckdb", zip_paths=[zip1], allow_download=False
    )
    c.ensure_loaded()
    yield c
    c.close()


@pytest.fixture
def two_zip_connector(tmp_path):
    zip1 = _make_zip(tmp_path / "public_split_1_10.zip", {"20260101_Public01.xml": _XML})
    zip2 = _make_zip(tmp_path / "public_split_11_20.zip", {"20260101_Public11.xml": _XML_2})
    c = ABNBulkExtractConnector(
        db_path=tmp_path / "bulk.duckdb", zip_paths=[zip1, zip2], allow_download=False
    )
    c.ensure_loaded()
    yield c
    c.close()


class TestLoad:
    def test_loads_all_records(self, connector):
        assert connector.row_count() == 4

    def test_lookup_preserves_abn_and_leading_zero_acn(self, connector):
        rec = connector.lookup_abn("11004000001")
        assert rec is not None
        assert rec["abn"] == "11004000001"
        assert rec["acn"] == "004000001"

    def test_sole_trader_name_assembled_no_acn(self, connector):
        rec = connector.lookup_abn("22000000002")
        assert rec["org_name"] == "JOHN QUINCY SMITH"
        assert rec["acn"] is None
        assert rec["state"] == "QLD"
        assert rec["postcode"] == "4000"

    def test_trading_names_round_trip(self, connector):
        rec = connector.lookup_abn("11004000001")
        assert rec["trading_names"] == ["Acme Widgets", "Widget World"]

    def test_dates_converted_to_iso(self, connector):
        rec = connector.lookup_abn("11004000001")
        assert rec["status_effective_from"] == "2000-02-24"

    def test_lookup_normalizes_input(self, connector):
        assert connector.lookup_abn("11 004 000 001") is not None
        assert connector.lookup_abn("99999999999") is None


class TestFetch:
    def test_excludes_cancelled_by_default(self, connector):
        abns = {r["abn"] for r in connector.fetch({})}
        assert "33000000003" not in abns
        assert len(abns) == 3

    def test_cancelled_included_when_requested(self, connector):
        abns = {r["abn"] for r in connector.fetch({"abn_status": "CAN"})}
        assert abns == {"33000000003"}

    def test_state_filter(self, connector):
        abns = {r["abn"] for r in connector.fetch({"state": "QLD"})}
        assert abns == {"22000000002", "44000000004"}

    def test_postcode_filter(self, connector):
        abns = {r["abn"] for r in connector.fetch({"postcode": "4101"})}
        assert abns == {"44000000004"}

    def test_entity_type_filter(self, connector):
        abns = {r["abn"] for r in connector.fetch({"entity_types": ["IND"]})}
        assert abns == {"22000000002"}

    def test_asic_codes_translate_to_abr(self, connector):
        abns = {r["abn"] for r in connector.fetch({"entity_types": ["APTY"]})}
        assert abns == {"11004000001"}

    def test_min_years_filter(self, connector):
        # The trust registered in 2023 must be excluded at min_years=20.
        abns = {r["abn"] for r in connector.fetch({"min_years": 20})}
        assert "44000000004" not in abns
        assert "11004000001" in abns

    def test_limit(self, connector):
        assert len(connector.fetch({"limit": 1})) == 1


class TestTwoZips:
    def test_union_loads_and_duplicate_abn_kept_once(self, two_zip_connector):
        # 4 records in zip1 + 2 in zip2, one of which duplicates an ABN.
        assert two_zip_connector.row_count() == 5

    def test_duplicate_abn_last_write_wins(self, two_zip_connector):
        rec = two_zip_connector.lookup_abn("11004000001")
        assert rec["state"] == "TAS"  # zip2's updated address replaced zip1's


class TestNormalize:
    def test_private_company(self, connector):
        company = connector.normalize(connector.lookup_abn("11004000001"))
        assert company.entity_id == "abn:11004000001"
        assert company.abn == "11004000001"
        assert company.acn == "004000001"
        assert company.legal_name == "ACME WIDGETS PTY LTD"
        assert company.trading_names == ["Acme Widgets", "Widget World"]
        assert company.location.state == "VIC"
        assert company.location.postcode == "3000"
        assert company.age.abn_registered == "2000-02-24"
        assert company.age.years_operating >= 25
        assert company.ownership.structure_guess == "private-company"
        assert all(p.source == "abn_bulk_extract" for p in company.provenance)
        assert all(p.confidence == 0.95 for p in company.provenance)

    def test_sole_trader_structure_guess(self, connector):
        company = connector.normalize(connector.lookup_abn("22000000002"))
        assert company.ownership.structure_guess == "sole-trader"

    def test_trust_structure_guess(self, connector):
        company = connector.normalize(connector.lookup_abn("44000000004"))
        assert company.ownership.structure_guess == "trust"


class TestDownloadGuard:
    def test_missing_zips_no_download_raises(self, tmp_path):
        c = ABNBulkExtractConnector(
            db_path=tmp_path / "bulk.duckdb",
            data_dir=str(tmp_path / "empty"),
            allow_download=False,
        )
        with pytest.raises(FileNotFoundError, match="ABN_BULK_DOWNLOAD"):
            c.ensure_loaded()
        c.close()

    def test_explicit_path_missing_raises(self, tmp_path):
        c = ABNBulkExtractConnector(
            db_path=tmp_path / "bulk.duckdb",
            zip_paths=[str(tmp_path / "nope.zip")],
            allow_download=False,
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            c.ensure_loaded()
        c.close()
