"""Offline unit tests for the EntityResolver (plan §6) — both connectors mocked.

Candidates use this repo's RawRecord shape (``org_name`` / ``state`` / ``postcode``).
"""
from __future__ import annotations

from sourcing.enrichment.entity_resolution import EntityResolver
from sourcing.models.company import CompanyRecord, Location


class FakeAPI:
    def __init__(self, candidates):
        self._candidates = candidates

    def fetch(self, params):
        return self._candidates


class FakeASIC:
    def __init__(self, spine_by_abn):
        self._spine = spine_by_abn

    def lookup_abn(self, abn):
        return self._spine.get(abn)


def _resolver(candidates, spine=None):
    return EntityResolver(api=FakeAPI(candidates), asic=FakeASIC(spine or {}))


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------

def test_resolve_accepts_high_confidence():
    r = _resolver([
        {"abn": "11111111111", "org_name": "brisbane materials testing",
         "postcode": "4101", "state": "QLD"},
    ])
    abn, rc = r.resolve("Brisbane Materials Testing Pty Ltd", "4101", "QLD")
    assert abn == "11111111111"
    assert rc >= 0.85


def test_resolve_unresolved_below_threshold():
    r = _resolver([
        {"abn": "22222222222", "org_name": "totally different co",
         "postcode": "2000", "state": "NSW"},
    ])
    abn, rc = r.resolve("Brisbane Materials Testing", "4101", "QLD")
    assert abn is None
    assert rc < 0.85


def test_resolve_keeps_uncertain_band():
    # Strong name, but wrong postcode + state → lands in 0.60–0.85 keep band.
    r = _resolver([
        {"abn": "33333333333", "org_name": "brisbane materials testing",
         "postcode": "9999", "state": "NSW"},
    ])
    abn, rc = r.resolve("Brisbane Materials Testing", "4101", "QLD")
    assert abn == "33333333333"
    assert 0.60 <= rc < 0.85


def test_resolve_no_candidates():
    r = _resolver([])
    abn, rc = r.resolve("Anything", "4000", "QLD")
    assert abn is None and rc == 0.0


# ---------------------------------------------------------------------------
# enrich()
# ---------------------------------------------------------------------------

def _record(name, postcode, state):
    return CompanyRecord(
        entity_id=f"maps:{name}",
        legal_name=name,
        location=Location(postcode=postcode, state=state),
    )


def test_enrich_merges_spine_fields_on_accept():
    r = _resolver(
        candidates=[{"abn": "11111111111", "org_name": "brisbane materials testing",
                     "postcode": "4101", "state": "QLD"}],
        spine={"11111111111": {"acn": "111111111", "org_name": "BRISBANE MATERIALS TESTING PTY LTD",
                               "status_effective_from": "2001-05-01"}},
    )
    rec = _record("Brisbane Materials Testing Pty Ltd", "4101", "QLD")
    out = r.enrich(rec)
    assert out.abn == "11111111111"
    assert out.acn == "111111111"
    assert out.legal_name == "BRISBANE MATERIALS TESTING PTY LTD"   # register name wins
    assert out.age.asic_registered == "2001-05-01"
    assert "unresolved_abn" not in out.flags
    assert out.resolution_confidence >= 0.85
    assert any(p.field == "abn" for p in out.provenance)


def test_enrich_flags_unresolved():
    r = _resolver([{"abn": "22222222222", "org_name": "totally different co",
                    "postcode": "2000", "state": "NSW"}])
    rec = _record("Brisbane Materials Testing", "4101", "QLD")
    out = r.enrich(rec)
    assert out.abn is None
    assert "unresolved_abn" in out.flags


def test_enrich_flags_uncertain_band():
    r = _resolver(
        candidates=[{"abn": "33333333333", "org_name": "brisbane materials testing",
                     "postcode": "9999", "state": "NSW"}],
        spine={"33333333333": {"acn": "333333333", "org_name": "BMT PTY LTD",
                               "status_effective_from": "1999-01-01"}},
    )
    rec = _record("Brisbane Materials Testing", "4101", "QLD")
    out = r.enrich(rec)
    assert out.abn == "33333333333"
    assert "abn_match_uncertain" in out.flags


def test_enrich_skips_records_that_already_have_abn():
    r = _resolver([])
    rec = CompanyRecord(entity_id="abn:99999999999", abn="99999999999", legal_name="Already Known")
    out = r.enrich(rec)
    assert out.abn == "99999999999"
    assert out.flags == []
