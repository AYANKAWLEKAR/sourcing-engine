"""Unit tests for API connectors — ABN Lookup (plan §5 api connectors).

The transport is mocked to return JSONP envelopes; no network. Guards that the
connector inherits from APIConnector (not the Protocol directly) and that the
detail + name-match JSON shapes normalize correctly.
"""
from __future__ import annotations

import json

import pytest

from sourcing.connectors.abn.lookup import ABNLookupAPIConnector
from sourcing.connectors.base_api import APIConnector

_DETAIL_JSON = {
    "Abn": "51824753556",
    "AbnStatus": "Active",
    "AbnStatusEffectiveFrom": "2000-03-29",
    "Acn": "124153160",
    "AddressState": "VIC",
    "AddressPostcode": "3000",
    "BusinessName": ["Xero"],
    "EntityName": "XERO AUSTRALIA PTY LTD",
    "EntityTypeCode": "PRV",
    "EntityTypeName": "Australian Private Company",
    "Message": "",
}

_NAMES_JSON = {
    "Names": [
        {
            "Abn": "51824753556",
            "AbnStatus": "Active",
            "IsCurrent": True,
            "Name": "XERO AUSTRALIA PTY LTD",
            "NameType": "Entity Name",
            "Postcode": "3000",
            "Score": 100,
            "State": "VIC",
        },
        {
            "Abn": "99999999999",
            "AbnStatus": "Active",
            "IsCurrent": True,
            "Name": "XERO CONSULTING PTY LTD",
            "NameType": "Trading Name",
            "Postcode": "2000",
            "Score": 78,
            "State": "NSW",
        },
    ],
    "Message": "",
}

_NOT_FOUND_JSON = {"Abn": "", "Message": "Search text is not a valid ABN"}


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        # Wrap as JSONP to also exercise the unwrap path.
        self.text = f"callback({json.dumps(payload)})"

    def raise_for_status(self) -> None:
        pass


def _transport_for(payload_by_url: dict):
    def transport(url, params=None, headers=None, timeout=None):
        for fragment, payload in payload_by_url.items():
            if fragment in url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected url {url}")

    return transport


def _connector(payload_by_url: dict) -> ABNLookupAPIConnector:
    return ABNLookupAPIConnector(
        guid="test-guid",
        transport=_transport_for(payload_by_url),
    )


# ---------------------------------------------------------------------------
# Inheritance guard (plan §5: prevents re-implementing the Protocol directly)
# ---------------------------------------------------------------------------

def test_abn_lookup_inherits_apiconnector():
    c = ABNLookupAPIConnector(guid="x")
    assert isinstance(c, APIConnector)


def test_missing_guid_raises():
    with pytest.raises(ValueError, match="ABN_LOOKUP_GUID"):
        ABNLookupAPIConnector(guid="")


# ---------------------------------------------------------------------------
# Detail mode
# ---------------------------------------------------------------------------

def test_detail_fetch_and_normalize():
    c = _connector({"AbnDetails": _DETAIL_JSON})
    records = c.fetch({"abn": "51 824 753 556"})
    assert len(records) == 1
    company = c.normalize(records[0])
    assert company.abn == "51824753556"
    assert company.acn == "124153160"
    assert company.legal_name == "XERO AUSTRALIA PTY LTD"
    assert company.location.state == "VIC"
    assert company.location.postcode == "3000"
    assert company.ownership.structure_guess == "private-company"
    assert company.age.years_operating is not None and company.age.years_operating >= 20
    assert "Xero" in company.trading_names


def test_detail_not_found_returns_empty():
    c = _connector({"AbnDetails": _NOT_FOUND_JSON})
    assert c.fetch({"abn": "00000000000"}) == []


# ---------------------------------------------------------------------------
# Name-match mode (the resolver bridge)
# ---------------------------------------------------------------------------

def test_name_match_returns_scored_candidates():
    c = _connector({"MatchingNames": _NAMES_JSON})
    records = c.fetch({"name": "Xero", "state": "VIC"})
    assert len(records) == 2
    abns = {r["abn"] for r in records}
    assert abns == {"51824753556", "99999999999"}
    # Score is preserved in raw for the resolver.
    top = next(r for r in records if r["abn"] == "51824753556")
    assert top["raw"]["Score"] == 100


def test_unsupported_params_raises():
    c = _connector({})
    with pytest.raises(ValueError, match="Expected 'abn' or 'name'"):
        c.fetch({"postcode": "2000"})
