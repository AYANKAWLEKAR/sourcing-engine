"""Conversational re-rank — turn a natural-language follow-up into a deterministic
filter + sort over an already-ranked shortlist.

After a run completes the user can keep the conversation going ("out of these,
only the ones with government contracts", "sort by EBITDA"). We do NOT re-scrape
or re-score: one LLM call maps the request to a structured :class:`QuerySpec` over
a whitelist of record fields, then we apply it deterministically in Python so the
statistical/evidence fit metrics stay intact and nothing is fabricated.

Operates on the persisted shortlist dumps (``RankedCompany.model_dump``) so it
works directly on what the store/API already hold.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..llm import LLMClient, complete_json, get_llm_client

# Whitelist: query field name → path into the RankedCompany dump. A leading
# "record." path digs into the nested CompanyRecord; bare names are top-level
# score fields. List-valued fields support the ``contains`` operator.
FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "state": ("record", "location", "state"),
    "postcode": ("record", "location", "postcode"),
    "suburb": ("record", "location", "suburb"),
    "anzsic": ("record", "sector", "anzsic"),
    "keyword_hits": ("record", "sector", "keyword_hits"),
    "business_model": ("record", "business_model"),
    "years_operating": ("record", "age", "years_operating"),
    "employee_count": ("record", "size", "employee_count"),
    "revenue_est_aud": ("record", "size", "revenue_est_aud"),
    "ebitda_est_aud": ("record", "size", "ebitda_est_aud"),
    "gov_contracts": ("record", "moat_signals", "gov_contracts"),
    "gov_contract_value_aud": ("record", "moat_signals", "gov_contract_value_aud"),
    "award_finalist": ("record", "moat_signals", "award_finalist"),
    "regulatory_accreditation": ("record", "moat_signals", "regulatory_accreditation"),
    "ip": ("record", "moat_signals", "ip"),
    "s_final": ("s_final",),
    "s_stat": ("s_stat",),
    "s_evidence": ("s_evidence",),
    "judge_fit": ("judge_fit",),
}

_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "is_true", "is_false", "exists"}

_SYSTEM = (
    "You translate an analyst's follow-up request into a JSON filter+sort over an "
    "existing company shortlist. Use ONLY these fields: "
    + ", ".join(FIELD_PATHS)
    + ". Operators: eq, ne, gt, gte, lt, lte (numbers), contains (list/text membership), "
    "in (value is a list), is_true, is_false (booleans), exists. "
    "Return ONLY this JSON: "
    '{"filters": [{"field": "...", "op": "...", "value": ...}], '
    '"sort_by": "s_final", "order": "desc"}. '
    "Omit value for is_true/is_false/exists. If the request implies no sort, use s_final desc. "
    "Money like '$2M' is 2000000. Never invent fields."
)


class Filter(BaseModel):
    field: str
    op: str
    value: Any = None


class QuerySpec(BaseModel):
    filters: list[Filter] = Field(default_factory=list)
    sort_by: str = "s_final"
    order: str = "desc"


def parse_query(
    text: str,
    buybox_thesis: str = "",
    *,
    llm: LLMClient | None = None,
    model: str | None = None,
) -> QuerySpec:
    """One LLM call: natural language → a validated :class:`QuerySpec`."""
    from ..config import get_settings

    llm = llm or get_llm_client()
    model = model or get_settings().enrich_model
    user = text if not buybox_thesis else f"Buy-box context: {buybox_thesis}\n\nRequest: {text}"
    data = complete_json(llm, model, _SYSTEM, user)
    return _validate_spec(data)


def _validate_spec(data: dict) -> QuerySpec:
    filters: list[Filter] = []
    for f in data.get("filters") or []:
        if not isinstance(f, dict):
            continue
        field = f.get("field")
        op = f.get("op")
        if field in FIELD_PATHS and op in _OPS:
            filters.append(Filter(field=field, op=op, value=f.get("value")))
    sort_by = data.get("sort_by")
    if sort_by not in FIELD_PATHS:
        sort_by = "s_final"
    order = "asc" if str(data.get("order", "desc")).lower() == "asc" else "desc"
    return QuerySpec(filters=filters, sort_by=sort_by, order=order)


def _dig(item: dict, path: tuple[str, ...]) -> Any:
    cur: Any = item
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _matches(value: Any, op: str, target: Any) -> bool:
    if op == "exists":
        return value is not None
    if op == "is_true":
        return value is True
    if op == "is_false":
        return value is False
    if value is None:
        return False  # honest: a missing field never satisfies a positive predicate
    try:
        if op == "eq":
            return _norm(value) == _norm(target)
        if op == "ne":
            return _norm(value) != _norm(target)
        if op == "gt":
            return float(value) > float(target)
        if op == "gte":
            return float(value) >= float(target)
        if op == "lt":
            return float(value) < float(target)
        if op == "lte":
            return float(value) <= float(target)
        if op == "contains":
            if isinstance(value, list):
                return any(_norm(target) == _norm(v) or _norm(target) in _norm(v) for v in value)
            return _norm(target) in _norm(value)
        if op == "in":
            targets = target if isinstance(target, list) else [target]
            return any(_norm(value) == _norm(t) for t in targets)
    except (TypeError, ValueError):
        return False
    return False


def _norm(v: Any) -> Any:
    return v.lower().strip() if isinstance(v, str) else v


def apply_query(shortlist: list[dict], spec: QuerySpec) -> list[dict]:
    """Filter then sort the shortlist dumps deterministically per the spec."""
    out = []
    for item in shortlist:
        if all(_matches(_dig(item, FIELD_PATHS[f.field]), f.op, f.value) for f in spec.filters):
            out.append(item)

    path = FIELD_PATHS[spec.sort_by]
    reverse = spec.order == "desc"

    def sort_key(item: dict) -> tuple[int, float]:
        val = _dig(item, path)
        # None sorts last regardless of direction.
        if val is None:
            return (1, 0.0)
        try:
            return (0, float(val))
        except (TypeError, ValueError):
            return (0, 0.0)

    out.sort(key=sort_key, reverse=reverse)
    if reverse:  # keep None-last after a reverse sort
        out.sort(key=lambda i: 0 if _dig(i, path) is not None else 1)
    return out
