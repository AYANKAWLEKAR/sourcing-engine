"""Unit tests for the ABR XML parser helpers (parser.py) — no network calls.

The live connector uses the JSONP API (see test_api_connectors.py); these tests
cover the reusable XML parsing/normalisation utilities:
  * XML parsing: businessEntity and searchResultsRecord shapes
  * normalisation: Pty Ltd, individual, years_operating date arithmetic
  * filtering: cancelled entities dropped at parse time
  * error handling: ABRException on API <exception> elements
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from sourcing.connectors.abn.parser import (
    ABRException,
    calc_years_operating,
    normalize_to_company_record,
    parse_response,
)

NS = "http://abr.business.gov.au/ABRXMLSearch/"


# ---------------------------------------------------------------------------
# XML fixtures
# ---------------------------------------------------------------------------

def _wrap(body: str, ns: str = NS) -> bytes:
    """Wrap a response body in the ABRPayloadSearchResults envelope."""
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<ABRPayloadSearchResults xmlns="{ns}">'
        f"  <response>{body}</response>"
        f"</ABRPayloadSearchResults>"
    ).encode()


ACTIVE_ENTITY_XML = _wrap("""
<businessEntity>
  <ABN>
    <identifierValue>51824753556</identifierValue>
    <isCurrentIndicator>Y</isCurrentIndicator>
  </ABN>
  <entityStatus>
    <entityStatusCode>Active</entityStatusCode>
    <effectiveFrom>2003-07-01</effectiveFrom>
    <effectiveTo>0001-01-01</effectiveTo>
  </entityStatus>
  <ASICNumber>123456789</ASICNumber>
  <entityType>
    <entityTypeCode>PRV</entityTypeCode>
    <entityDescription>Australian Private Company</entityDescription>
  </entityType>
  <mainName>
    <organisationName>ACME TESTING PTY LTD</organisationName>
    <effectiveFrom>2003-07-01</effectiveFrom>
  </mainName>
  <mainTradingName>
    <organisationName>Acme Testing</organisationName>
    <effectiveFrom>2003-07-01</effectiveFrom>
  </mainTradingName>
  <mainBusinessPhysicalAddress>
    <stateCode>QLD</stateCode>
    <postcode>4000</postcode>
    <effectiveFrom>2003-07-01</effectiveFrom>
    <effectiveTo>0001-01-01</effectiveTo>
  </mainBusinessPhysicalAddress>
</businessEntity>
""")

CANCELLED_ENTITY_XML = _wrap("""
<businessEntity>
  <ABN><identifierValue>12000000001</identifierValue></ABN>
  <entityStatus>
    <entityStatusCode>Cancelled</entityStatusCode>
    <effectiveFrom>2000-01-01</effectiveFrom>
  </entityStatus>
  <entityType>
    <entityTypeCode>PRV</entityTypeCode>
    <entityDescription>Australian Private Company</entityDescription>
  </entityType>
  <mainName>
    <organisationName>GONE PTY LTD</organisationName>
  </mainName>
  <mainBusinessPhysicalAddress>
    <stateCode>NSW</stateCode>
    <postcode>2000</postcode>
  </mainBusinessPhysicalAddress>
</businessEntity>
""")

INDIVIDUAL_ENTITY_XML = _wrap("""
<businessEntity>
  <ABN><identifierValue>98765432100</identifierValue></ABN>
  <entityStatus>
    <entityStatusCode>Active</entityStatusCode>
    <effectiveFrom>2010-04-15</effectiveFrom>
  </entityStatus>
  <entityType>
    <entityTypeCode>IND</entityTypeCode>
    <entityDescription>Individual/Sole Trader</entityDescription>
  </entityType>
  <legalName>
    <givenName>Jane</givenName>
    <otherGivenName>Marie</otherGivenName>
    <familyName>Smith</familyName>
    <effectiveFrom>2010-04-15</effectiveFrom>
  </legalName>
  <mainTradingName>
    <organisationName>Jane Smith Consulting</organisationName>
  </mainTradingName>
  <mainBusinessPhysicalAddress>
    <stateCode>NSW</stateCode>
    <postcode>2000</postcode>
  </mainBusinessPhysicalAddress>
</businessEntity>
""")

POSTCODE_RESULTS_XML = _wrap("""
<searchResultsList>
  <numberOfRecords>2</numberOfRecords>
  <searchResultsRecord>
    <ABN><identifierValue>11111111111</identifierValue><isCurrentIndicator>Y</isCurrentIndicator></ABN>
    <ABNStatus>Active</ABNStatus>
    <ABNStatusEffectiveFrom>2005-06-01</ABNStatusEffectiveFrom>
    <entityType>
      <entityTypeCode>PRV</entityTypeCode>
      <entityDescription>Australian Private Company</entityDescription>
    </entityType>
    <mainName>
      <organisationName>FIRST COMPANY PTY LTD</organisationName>
    </mainName>
    <mainTradingName>
      <organisationName>First Co</organisationName>
    </mainTradingName>
    <mainBusinessPhysicalAddress>
      <stateCode>QLD</stateCode>
      <postcode>4000</postcode>
    </mainBusinessPhysicalAddress>
  </searchResultsRecord>
  <searchResultsRecord>
    <ABN><identifierValue>22222222222</identifierValue></ABN>
    <ABNStatus>Cancelled</ABNStatus>
    <ABNStatusEffectiveFrom>2001-01-01</ABNStatusEffectiveFrom>
    <entityType>
      <entityTypeCode>PRV</entityTypeCode>
      <entityDescription>Australian Private Company</entityDescription>
    </entityType>
    <mainName><organisationName>CANCELLED CO PTY LTD</organisationName></mainName>
    <mainBusinessPhysicalAddress>
      <stateCode>QLD</stateCode>
      <postcode>4000</postcode>
    </mainBusinessPhysicalAddress>
  </searchResultsRecord>
</searchResultsList>
""")

ERROR_RESPONSE_XML = _wrap("""
<exception>
  <exceptionDescription>Search text is not a valid ABN or ACN</exceptionDescription>
</exception>
""")


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_active_entity_returns_one_record(self):
        records = parse_response(ACTIVE_ENTITY_XML)
        assert len(records) == 1
        r = records[0]
        assert r["abn"] == "51824753556"
        assert r["acn"] == "123456789"
        assert r["org_name"] == "ACME TESTING PTY LTD"
        assert r["trading_names"] == ["Acme Testing"]
        assert r["state"] == "QLD"
        assert r["postcode"] == "4000"
        assert r["entity_type_code"] == "PRV"
        assert r["status_code"] == "Active"
        assert r["status_effective_from"] == "2003-07-01"

    def test_cancelled_entity_is_dropped(self):
        records = parse_response(CANCELLED_ENTITY_XML)
        assert records == []

    def test_individual_entity_resolves_name_parts(self):
        records = parse_response(INDIVIDUAL_ENTITY_XML)
        assert len(records) == 1
        r = records[0]
        assert r["abn"] == "98765432100"
        assert r["entity_type_code"] == "IND"
        assert r["org_name"] is None
        assert r["given_name"] == "Jane"
        assert r["other_given_name"] == "Marie"
        assert r["family_name"] == "Smith"
        assert r["trading_names"] == ["Jane Smith Consulting"]

    def test_postcode_results_filters_cancelled(self):
        records = parse_response(POSTCODE_RESULTS_XML)
        assert len(records) == 1  # only the Active one
        assert records[0]["abn"] == "11111111111"

    def test_postcode_results_status_effective_from(self):
        records = parse_response(POSTCODE_RESULTS_XML)
        assert records[0]["status_effective_from"] == "2005-06-01"

    def test_error_response_raises_abr_exception(self):
        with pytest.raises(ABRException, match="not a valid ABN"):
            parse_response(ERROR_RESPONSE_XML)

    def test_source_id_injected(self):
        records = parse_response(ACTIVE_ENTITY_XML, source_id="test_source")
        assert records[0]["source_id"] == "test_source"

    def test_raw_dict_present(self):
        records = parse_response(ACTIVE_ENTITY_XML)
        assert isinstance(records[0]["raw"], dict)


# ---------------------------------------------------------------------------
# Normalisation tests
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_pty_ltd_company(self):
        raw = parse_response(ACTIVE_ENTITY_XML)[0]
        company = normalize_to_company_record(raw)
        assert company.entity_id == "abn:51824753556"
        assert company.abn == "51824753556"
        assert company.acn == "123456789"
        assert company.legal_name == "ACME TESTING PTY LTD"
        assert company.trading_names == ["Acme Testing"]
        assert company.country == "Australia"
        assert company.location.state == "QLD"
        assert company.location.postcode == "4000"
        assert company.ownership.structure_guess == "private-company"
        assert company.ownership.listed_entity is None  # PRV, not PUB

    def test_individual_builds_full_name(self):
        raw = parse_response(INDIVIDUAL_ENTITY_XML)[0]
        company = normalize_to_company_record(raw)
        assert company.legal_name == "Jane Marie Smith"
        assert company.ownership.structure_guess == "sole-trader"
        assert "Jane Smith Consulting" in company.trading_names

    def test_years_operating_calculated(self):
        raw = parse_response(ACTIVE_ENTITY_XML)[0]
        company = normalize_to_company_record(raw)
        # Registered 2003-07-01; should be 20+ years by now.
        assert company.age.abn_registered == "2003-07-01"
        assert company.age.years_operating is not None
        assert company.age.years_operating >= 20

    def test_provenance_populated(self):
        raw = parse_response(ACTIVE_ENTITY_XML)[0]
        company = normalize_to_company_record(raw)
        fields = {p.field for p in company.provenance}
        assert {"abn", "legal_name", "state", "years_operating"} <= fields
        for p in company.provenance:
            assert p.source == "abn_lookup_api"


# ---------------------------------------------------------------------------
# calc_years_operating
# ---------------------------------------------------------------------------

class TestCalcYearsOperating:
    def test_known_date(self):
        known = date(2003, 7, 1)
        expected = (date.today() - known).days // 365
        result = calc_years_operating("2003-07-01")
        assert result is not None
        assert abs(result - expected) <= 1  # rounding tolerance

    def test_sentinel_date_returns_none(self):
        assert calc_years_operating("0001-01-01") is None

    def test_none_returns_none(self):
        assert calc_years_operating(None) is None

    def test_invalid_string_returns_none(self):
        assert calc_years_operating("not-a-date") is None

    def test_very_recent_returns_zero(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert calc_years_operating(yesterday) == 0
