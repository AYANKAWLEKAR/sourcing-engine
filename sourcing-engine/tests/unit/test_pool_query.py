"""Unit tests for conversational re-rank (rank/pool_query) — offline, scripted LLM."""
from __future__ import annotations

import json

from sourcing.llm import LLMResponse
from sourcing.rank.pool_query import QuerySpec, apply_query, parse_query


class ScriptedLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    def chat(self, model, system, messages, tools=None, format=None):
        return LLMResponse(text=json.dumps(self._payload))


def _item(name, *, state="QLD", gov=False, ebitda=None, s_final=0.5, s_stat=50.0):
    return {
        "record": {
            "legal_name": name,
            "location": {"state": state, "postcode": "4000"},
            "sector": {"anzsic": ["3223"], "keyword_hits": ["hvac"]},
            "age": {"years_operating": 10},
            "size": {"ebitda_est_aud": ebitda, "employee_count": 20},
            "moat_signals": {"gov_contracts": gov, "award_finalist": False},
            "business_model": "B2B",
        },
        "s_final": s_final,
        "s_stat": s_stat,
        "s_evidence": 0.0,
        "judge_fit": 0.6,
    }


_SHORTLIST = [
    _item("GovCo", gov=True, ebitda=2_000_000, s_final=0.6),
    _item("PlainCo", gov=False, ebitda=4_000_000, s_final=0.8),
    _item("NoEbitdaCo", gov=True, ebitda=None, s_final=0.4),
]


def test_parse_query_whitelists_fields_and_ops():
    spec = parse_query(
        "only ones with government contracts",
        llm=ScriptedLLM({"filters": [{"field": "gov_contracts", "op": "is_true"}],
                         "sort_by": "s_final", "order": "desc"}),
    )
    assert spec.filters[0].field == "gov_contracts"
    assert spec.filters[0].op == "is_true"


def test_parse_query_drops_unknown_field():
    spec = parse_query(
        "nonsense",
        llm=ScriptedLLM({"filters": [{"field": "made_up_field", "op": "eq", "value": 1}],
                         "sort_by": "banana", "order": "asc"}),
    )
    assert spec.filters == []
    assert spec.sort_by == "s_final"  # invalid sort falls back
    assert spec.order == "asc"


def test_apply_filter_is_true():
    spec = QuerySpec.model_validate(
        {"filters": [{"field": "gov_contracts", "op": "is_true"}], "sort_by": "s_final", "order": "desc"}
    )
    out = apply_query(_SHORTLIST, spec)
    names = [i["record"]["legal_name"] for i in out]
    assert names == ["GovCo", "NoEbitdaCo"]  # gov=True only, sorted by s_final desc


def test_apply_numeric_filter_and_missing_is_nonmatch():
    spec = QuerySpec.model_validate(
        {"filters": [{"field": "ebitda_est_aud", "op": "gte", "value": 3_000_000}],
         "sort_by": "s_final", "order": "desc"}
    )
    out = apply_query(_SHORTLIST, spec)
    names = [i["record"]["legal_name"] for i in out]
    assert names == ["PlainCo"]  # NoEbitdaCo (None) excluded, GovCo below threshold


def test_apply_sort_by_ebitda_puts_none_last():
    spec = QuerySpec.model_validate(
        {"filters": [], "sort_by": "ebitda_est_aud", "order": "desc"}
    )
    out = apply_query(_SHORTLIST, spec)
    names = [i["record"]["legal_name"] for i in out]
    assert names[0] == "PlainCo"      # 4M
    assert names[1] == "GovCo"        # 2M
    assert names[-1] == "NoEbitdaCo"  # None sorts last


def test_apply_contains_on_keyword_list():
    spec = QuerySpec.model_validate(
        {"filters": [{"field": "keyword_hits", "op": "contains", "value": "hvac"}],
         "sort_by": "s_final", "order": "desc"}
    )
    assert len(apply_query(_SHORTLIST, spec)) == 3
