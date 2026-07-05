"""Unit tests for the ShortlistGate (Part C) — offline, fakes only."""
from __future__ import annotations

from sourcing.models.company import CompanyRecord, Location
from sourcing.models.ranking import RankedCompany
from sourcing.models.source import SourceRegistryEntry
from sourcing.runs.shortlist_gate import ShortlistGate


def _entry(enabled: bool) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        source_id="linkedin_headcount",
        connector_type="scrape",
        fields_provided=["employee_count"],
        sectors_covered=["all"],
        geo_granularity="national",
        join_key="name",
        cost_tier="metered",
        freshness="on_demand",
        reliability="text_low",
        enabled=enabled,
        gate="shortlist_only",
        connector_ref="fake.ref.LinkedIn",
        capability_doc="fake",
    )


def _rc(name: str = "Acme Air", anzsic: str | None = "3223") -> RankedCompany:
    rec = CompanyRecord(entity_id=f"abn:{name}", abn="1" * 11, legal_name=name,
                        location=Location(state="QLD"))
    if anzsic:
        rec.sector.anzsic = [anzsic]
    return RankedCompany(record=rec, s_stat=60.0, s_final=0.6)


class FakeLinkedIn:
    def __init__(self, count: int | None = 42):
        self._count = count

    def fetch(self, params):
        return [{"companyName": params["companyName"], "employeeCount": self._count}] if self._count else []

    def normalize(self, raw):
        from sourcing.models.company import Provenance, Size

        return CompanyRecord(
            entity_id="li:x",
            size=Size(employee_count=raw.get("employeeCount"), employee_source="linkedin"),
            provenance=[Provenance(field="employee_count", source="linkedin_headcount", confidence=0.7)],
        )


class FakeConnRegistry:
    def __init__(self, connector):
        self._connector = connector

    def get_or_create(self, ref):
        return self._connector


def test_linkedin_disabled_skips_fetch_but_estimator_flags():
    gate = ShortlistGate([_entry(enabled=False)], top_n=5)
    rc = _rc()
    gate.apply([rc])
    # No headcount fetched, so the proxy estimator records the honest flag...
    assert "unverified:ebitda_aud:no_employee_count" in rc.record.flags
    assert rc.record.size.ebitda_est_aud is None
    # ...and the deferred checklist was rebuilt to include it.
    assert any("no_employee_count" in item for item in rc.deferred_assessment)


def test_linkedin_enabled_merges_headcount_and_proxy_estimates():
    gate = ShortlistGate(
        [_entry(enabled=True)],
        connector_registry=FakeConnRegistry(FakeLinkedIn(count=42)),
        top_n=5,
    )
    rc = _rc()
    gate.apply([rc])
    rec = rc.record
    assert rec.size.employee_count == 42
    assert rec.size.employee_source == "linkedin"
    assert rec.size.ebitda_est_aud and rec.size.ebitda_est_aud > 0   # proxy ran
    assert rec.size.ebitda_confidence <= 0.4                          # capped low
    assert any(p.source == "linkedin_headcount" for p in rec.provenance)


def test_gate_respects_top_n():
    gate = ShortlistGate([_entry(enabled=False)], top_n=1)
    first, second = _rc("First"), _rc("Second")
    gate.apply([first, second])
    assert "unverified:ebitda_aud:no_employee_count" in first.record.flags
    assert "unverified:ebitda_aud:no_employee_count" not in second.record.flags


def test_gate_no_linkedin_entry_at_all():
    gate = ShortlistGate([], top_n=5)   # registry without linkedin
    rc = _rc()
    gate.apply([rc])                    # no crash; estimator still runs
    assert "unverified:ebitda_aud:no_employee_count" in rc.record.flags
