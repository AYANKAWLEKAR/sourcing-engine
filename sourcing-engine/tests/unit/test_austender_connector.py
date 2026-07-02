"""Unit tests for AusTenderConnector (addendum §6.1) — offline, mocked transport."""
from __future__ import annotations

import pytest

from sourcing.connectors.austender import AusTenderConnector
from sourcing.connectors.base_api import APIConnector
from sourcing.models.company import CompanyRecord

# Mirrors the real AusTender OCDS shape: supplier ABN in additionalIdentifiers,
# buyer role is "procuringEntity", value in contracts[].value.amount.
FIXTURE_RELEASE = {
    "ocid": "ocds-au-cn1234567",
    "parties": [
        {"name": "Acme Defence Pty Ltd",
         "additionalIdentifiers": [{"id": "11223344556", "scheme": "AU-ABN"}],
         "roles": ["supplier"]},
        {"name": "Department of Defence",
         "additionalIdentifiers": [{"id": "68706814312", "scheme": "AU-ABN"}],
         "roles": ["procuringEntity"]},
    ],
    "contracts": [
        {"id": "CN1234567", "value": {"amount": 1_250_000, "currency": "AUD"}},
        {"id": "CN1234568", "value": {"amount": 500_000, "currency": "AUD"}},
    ],
}


def test_inherits_apiconnector():
    assert isinstance(AusTenderConnector(), APIConnector)


def test_normalize_sets_signal_and_value():
    rec = AusTenderConnector().normalize(FIXTURE_RELEASE)
    assert rec.moat_signals.gov_contracts is True
    assert rec.moat_signals.gov_contract_value_aud == 1_750_000
    assert rec.abn == "11223344556"
    assert any(p.source == "austender" and p.confidence == 0.95 for p in rec.provenance)


def test_normalize_handles_missing_value():
    release = {"ocid": "x", "parties": [], "contracts": [{"id": "y"}]}
    rec = AusTenderConnector().normalize(release)
    assert rec.moat_signals.gov_contract_value_aud == 0


def test_fetch_rejects_bad_abn(monkeypatch):
    c = AusTenderConnector()
    monkeypatch.setattr(c, "_get", lambda *a, **k: pytest.fail("should not call _get"))
    assert c.fetch({"abn": "not-an-abn"}) == []
    assert c.fetch({"abn": "123"}) == []


def test_enrich_record_aggregates_agencies(monkeypatch):
    c = AusTenderConnector()
    monkeypatch.setattr(c, "fetch", lambda p: [FIXTURE_RELEASE, FIXTURE_RELEASE])
    rec = CompanyRecord(abn="11223344556", legal_name="Acme Defence Pty Ltd")
    c.enrich_record(rec)
    assert rec.moat_signals.gov_contracts is True
    assert rec.moat_signals.gov_contract_count == 2
    assert rec.moat_signals.gov_contract_value_aud == 3_500_000
    assert "Department of Defence" in rec.moat_signals.gov_contract_agencies


def test_enrich_record_marks_clean(monkeypatch):
    c = AusTenderConnector()
    monkeypatch.setattr(c, "fetch", lambda p: [])
    rec = CompanyRecord(abn="11223344556", legal_name="No-Gov Pty Ltd")
    c.enrich_record(rec)
    assert "austender_checked_no_contracts" in rec.flags
    assert rec.moat_signals.gov_contracts is False


def test_enrich_record_skips_without_abn():
    c = AusTenderConnector()
    rec = CompanyRecord(legal_name="Unresolved Co")  # no abn
    c.enrich_record(rec)
    assert rec.moat_signals.gov_contracts is False
    assert "austender_checked_no_contracts" not in rec.flags
