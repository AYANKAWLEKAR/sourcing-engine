"""Unit tests for Part B — screen, score (exact math), judge, rank, buybox."""
from __future__ import annotations

import inspect
import json

import pytest

from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location, Ownership
from sourcing.rank import score as score_mod
from sourcing.rank.buybox import BuyBox
from sourcing.rank.judge import LLMJudge
from sourcing.rank.rank import rank_pool
from sourcing.rank.score import statistical_fit, unverified_gate_count
from sourcing.rank.screen import screen

_BB = BuyBox(thesis="HVAC installers in Brisbane", sector_keywords=["hvac", "air conditioning"],
             anzsic=["3223"], states=["QLD"], target_models=["B2B"], min_years=3,
             exclude_listed=True, exclude_pe_vc=True)


def _co(name, *, state="QLD", model="B2B", listed=None, pe_vc=None, yrs=10,
        kw=("hvac",), exclude=(), pc="4000", density=None):
    r = CompanyRecord(entity_id=f"x:{name}", abn="1" * 11, legal_name=name,
                      location=Location(state=state, postcode=pc), business_model=model,
                      ownership=Ownership(listed_entity=listed, pe_vc_backed=pe_vc))
    r.sector.category_text = ["HVAC contractor"]
    r.sector.keyword_hits = list(kw)
    r.sector.exclude_hits = list(exclude)
    r.sector.keyword_density = density
    r.age.years_operating = yrs
    return r


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def test_screen_excludes_listed():
    assert screen(_co("Listed", listed=True), _BB) is False


def test_screen_excludes_pe_vc():
    assert screen(_co("PE", pe_vc=True), _BB) is False


def test_screen_excludes_sector_hit():
    assert screen(_co("Retail", exclude=["retail storefront"]), _BB) is False


def test_screen_gates_young_company():
    assert screen(_co("Young", yrs=1), _BB) is False


def test_screen_passes_survivor():
    assert screen(_co("Good"), _BB) is True


def test_screen_unknown_years_passes_but_flags():
    r = _co("UnknownAge", yrs=None)
    assert screen(r, _BB) is True
    assert "unverified:years_operating" in r.flags


# ---------------------------------------------------------------------------
# Score — exact math
# ---------------------------------------------------------------------------

def test_score_exact_math_perfect_record():
    # density=1.0, anzsic match, state match, B2B match, all provenance conf=1.0
    r = _co("Perfect", kw=["hvac", "air conditioning"], density=1.0)
    r.sector.anzsic = ["3223"]
    r.provenance = []
    from sourcing.models.company import Provenance
    r.provenance.append(Provenance(field="abn", source="x", confidence=1.0))

    class IdentityEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]  # cosine = 1.0 for any pair

    s = statistical_fit(r, _BB, embedder=IdentityEmbedder())
    # s_sem=1, s_kw=1, s_code=1 -> s_sector=1 ; s_state=1 ; s_model=1 -> fit=1
    # mean_conf=1 -> dampener=(0.7+0.3*1)=1.0 ; no unverified -> *1 ; *100 = 100
    assert s == pytest.approx(100.0, abs=0.01)


def test_score_confidence_dampener_floor():
    # No provenance -> mean_confidence defaults 0.5 -> dampener = 0.7+0.15 = 0.85
    r = _co("NoProv", kw=["hvac", "air conditioning"], density=1.0)
    r.sector.anzsic = ["3223"]
    r.provenance = []

    class IdentityEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    s = statistical_fit(r, _BB, embedder=IdentityEmbedder())
    assert s == pytest.approx(85.0, abs=0.01)


def test_score_unverified_penalty_applies():
    r = _co("Unverified", kw=["hvac", "air conditioning"], density=1.0)
    r.sector.anzsic = ["3223"]
    r.flags.append("unverified:years_operating")
    from sourcing.models.company import Provenance
    r.provenance = [Provenance(field="abn", source="x", confidence=1.0)]

    class IdentityEmbedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    s = statistical_fit(r, _BB, embedder=IdentityEmbedder())
    assert unverified_gate_count(r) == 1
    assert s == pytest.approx(85.0, abs=0.01)  # 100 * 0.85^1


def test_score_no_removed_terms_in_source():
    """Guard: the locked model has no s_ai / s_frag / s_size / s_age, no proxy penalty.

    Scans identifiers (function names + Name nodes) via AST so the docstring,
    which legitimately *names* the forbidden terms, doesn't trip the check.
    """
    import ast

    tree = ast.parse(inspect.getsource(score_mod))
    identifiers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    identifiers |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("s_ai", "s_frag", "s_size", "s_age", "proxy_penalty"):
        assert forbidden not in identifiers, f"removed term {forbidden} reappeared in score.py"


def test_score_wrong_state_and_model_lower():
    good = _co("Good", state="QLD", model="B2B", density=0.5)
    bad = _co("Bad", state="NSW", model="B2C", density=0.5)
    assert statistical_fit(good, _BB) > statistical_fit(bad, _BB)


# ---------------------------------------------------------------------------
# Judge + rank
# ---------------------------------------------------------------------------

class FakeJudgeLLM:
    def __init__(self, fit):
        self._fit = fit

    def chat(self, model, system, messages, tools=None, format=None):
        return LLMResponse(text=json.dumps(
            {"fit": self._fit, "rationale": "fits the thesis", "standout_signals": ["NATA accreditation"]}))


def test_judge_parses_fit_and_signals():
    jr = LLMJudge(llm=FakeJudgeLLM(0.9), model="x").judge(_co("A"), _BB)
    assert jr.fit == 0.9
    assert "NATA accreditation" in jr.standout_signals


def test_rank_blends_and_orders():
    pool = [_co("Cool Air QLD", density=1.0), _co("NSW Air", state="NSW", density=0.5),
            _co("Listed", listed=True)]
    pool[0].sector.anzsic = ["3223"]
    pool[0].moat_signals.regulatory_accreditation = True  # a grounded standout signal
    ranked = rank_pool(pool, _BB, judge=LLMJudge(llm=FakeJudgeLLM(0.8), model="x"), top_k=5)
    names = [rc.record.legal_name for rc in ranked]
    assert "Listed" not in names                  # excluded before scoring
    assert names[0] == "Cool Air QLD"             # best statistical fit ranks first
    top = ranked[0]
    # S_final = 0.55*(s_stat/100) + 0.45*0.8
    assert top.s_final == pytest.approx(0.55 * (top.s_stat / 100) + 0.45 * 0.8, abs=0.001)
    # Standout signals are GROUNDED (from the record), not the judge's free text.
    assert "regulatory accreditation" in top.standout_signals
    assert isinstance(top.deferred_assessment, list)


def test_rank_standout_signals_are_grounded_not_hallucinated():
    """The judge inventing a fact must NOT leak into the card's standout chips."""
    pool = [_co("NoGovCo", density=0.9)]  # no gov contracts set on the record
    pool[0].sector.anzsic = ["3223"]

    class HallucinatingJudge:
        def chat(self, model, system, messages, tools=None, format=None):
            return LLMResponse(text=json.dumps(
                {"fit": 0.7, "rationale": "looks good",
                 "standout_signals": ["$5M government contracts"]}))  # invented

    ranked = rank_pool(pool, _BB, judge=LLMJudge(llm=HallucinatingJudge(), model="x"), top_k=1)
    # The fabricated gov-contract claim must not appear (record has no gov contracts).
    assert not any("government contract" in s.lower() for s in ranked[0].standout_signals)


def test_rank_diversity_caps_postcode():
    # 5 companies same postcode; cap=2 should limit them in the top slice.
    pool = [_co(f"Co{i}", pc="4000", density=0.9) for i in range(5)]
    for r in pool:
        r.sector.anzsic = ["3223"]
    ranked = rank_pool(pool, _BB, judge=LLMJudge(llm=FakeJudgeLLM(0.5), model="x"),
                       top_k=2, postcode_cap=2)
    assert len(ranked) == 2


# ---------------------------------------------------------------------------
# BuyBox.from_ruleset
# ---------------------------------------------------------------------------

def test_buybox_from_ruleset_extracts_fields():
    from sourcing.ruleset.loader import load_origo_ruleset

    rs = load_origo_ruleset()
    bb = BuyBox.from_ruleset(rs)
    # The base ruleset should at least expose exclude flags and a thesis/name.
    assert isinstance(bb.sector_keywords, list)
    assert bb.thesis
    assert bb.exclude_listed in (True, False)


# ---------------------------------------------------------------------------
# Proxy gate + standout signals + score edge cases (branch coverage)
# ---------------------------------------------------------------------------

def test_proxy_gate_fails_when_estimate_above_band():
    bb = BuyBox(states=["QLD"], ebitda_min=1_000_000, ebitda_max=15_000_000)
    r = _co("TooBig")
    r.size.ebitda_est_aud = 100_000_000  # way above max*1.5
    assert screen(r, bb) is False
    assert r.screen.status == "proxy_gated_out"


def test_proxy_gate_passes_without_estimate():
    bb = BuyBox(states=["QLD"], ebitda_min=1_000_000, ebitda_max=15_000_000)
    r = _co("NoEstimate")  # ebitda_est_aud is None
    assert screen(r, bb) is True


def test_standout_signals_from_gov_contracts():
    from sourcing.rank.judge import standout_signals

    r = _co("GovCo")
    r.moat_signals.gov_contracts = True
    r.moat_signals.gov_contract_value_aud = 1_750_000
    r.moat_signals.gov_contract_agencies = ["Department of Defence", "CASA"]
    r.moat_signals.regulatory_accreditation = True
    sigs = standout_signals(r)
    assert any("1,750,000" in s for s in sigs)
    assert any("agencies" in s for s in sigs)
    assert "regulatory accreditation" in sigs


def test_score_no_geo_or_model_constraint_is_neutral():
    from sourcing.rank.score import s_model, s_state

    bb = BuyBox()  # no states, no target_models
    r = _co("Anywhere", state="WA", model="B2C")
    assert s_state(r, bb) == 1.0
    assert s_model(r, bb) == 1.0


def test_score_mixed_model_partial_credit():
    from sourcing.rank.score import s_model

    bb = BuyBox(target_models=["B2B"])
    r = _co("Mixed", model="MIXED")
    assert s_model(r, bb) == 0.5
