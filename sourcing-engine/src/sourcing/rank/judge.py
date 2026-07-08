"""LLM judge — calibrated qualitative fit over the full record (plan §3.3).

Reads the whole CompanyRecord — including moat/award/gov-contract context that
carries NO statistical weight — and returns a 0–1 fit + a one-line rationale +
standout signals. One JSON call on the configured ``judge_model`` (Claude by
default); the LLM client is injectable for offline tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import get_settings
from ..llm import complete_json, get_llm_client

if TYPE_CHECKING:
    from ..llm import LLMClient
    from ..models.company import CompanyRecord
    from .buybox import BuyBox

_SYSTEM = (
    "You are an M&A analyst judging how well a company fits a buy box. "
    "Weigh qualitative signals (government contracts, accreditation, IP, awards) in context. "
    "Strongly demote entities that look like holding or investment vehicles rather than "
    "operating businesses (heed any HOLDING/INVESTMENT warning), and sub-scale companies "
    "whose operating age or size sits far outside a lower-mid-market SME. "
    "Respond with ONLY a single JSON object."
)

_PROMPT = """Buy box thesis: {thesis}
Target sector keywords: {keywords}
Target states: {states}

Company:
{summary}

Return ONLY this JSON:
{{"fit": 0.0, "rationale": "one sentence on why this fits or not",
  "standout_signals": ["short phrases, e.g. 'NATA accreditation', '$1.7M gov contracts'"]}}"""


@dataclass
class JudgeResult:
    fit: float = 0.0
    rationale: str = ""
    standout_signals: list[str] = field(default_factory=list)
    # Fix 11: True when the LLM returned unparseable output so callers can surface
    # a "judge result unverified" flag rather than silently treating fit=0 as assessed.
    unavailable: bool = False


class LLMJudge:
    def __init__(self, llm: LLMClient | None = None, model: str | None = None):
        self._llm = llm or get_llm_client()
        self._model = model or get_settings().judge_model

    def judge(self, record: CompanyRecord, buybox: BuyBox) -> JudgeResult:
        data = complete_json(
            self._llm,
            self._model,
            _SYSTEM,
            _PROMPT.format(
                thesis=buybox.thesis or "(none)",
                keywords=", ".join(buybox.sector_keywords) or "(none)",
                states=", ".join(buybox.states) or "(any)",
                summary=summarize(record),
            ),
        )
        if not data:
            return JudgeResult(fit=0.0, rationale="judge unavailable", standout_signals=[], unavailable=True)
        try:
            fit = float(data.get("fit", 0.0))
        except (TypeError, ValueError):
            fit = 0.0
        signals = data.get("standout_signals") or []
        return JudgeResult(
            fit=max(0.0, min(1.0, fit)),
            rationale=str(data.get("rationale", "")),
            standout_signals=[str(s) for s in signals if s],
        )


def summarize(record: CompanyRecord) -> str:
    """Compact, human-readable record summary for the judge prompt."""
    from .quality import OPERATING_ENTITY_FLAG

    m = record.moat_signals
    lines = [
        f"name: {record.legal_name}",
        f"location: {record.location.suburb or ''} {record.location.state or ''} {record.location.postcode or ''}".strip(),
        f"categories: {', '.join(record.sector.category_text[:4])}",
        f"keyword_hits: {', '.join(record.sector.keyword_hits[:6]) or '(none)'}",
        f"business_model: {record.business_model or 'UNKNOWN'}",
        f"years_operating: {record.age.years_operating if record.age.years_operating is not None else 'unknown'}",
    ]
    # Age/size/ownership context — the judge (unlocked) does the nuanced weighing
    # the locked statistical score deliberately omits.
    if record.ownership.structure_guess:
        lines.append(f"entity_structure: {record.ownership.structure_guess}")
    if record.size.employee_count:
        lines.append(f"employees: {record.size.employee_count}")
    if record.size.revenue_est_aud:
        kind = "direct" if (record.size.revenue_confidence or 0.0) > 0.4 else "rough estimate"
        lines.append(f"revenue_aud: ${record.size.revenue_est_aud:,.0f} ({kind})")
    if record.ownership.pe_vc_backed is True:
        lines.append("pe_vc_backed: YES")
    if OPERATING_ENTITY_FLAG in record.flags:
        lines.append(
            "WARNING: name or structure suggests a HOLDING/INVESTMENT vehicle, "
            "not an operating business — verify it actually trades before rating highly"
        )
    if m.gov_contracts and m.gov_contract_value_aud:
        lines.append(f"gov_contracts: ${m.gov_contract_value_aud:,} across {m.gov_contract_count or 0} releases")
    if m.regulatory_accreditation:
        lines.append("regulatory_accreditation: yes")
    if m.ip:
        lines.append("ip: yes")
    if m.award_finalist:
        lines.append("award_finalist: yes")
    return "\n".join(lines)


def standout_signals(record: CompanyRecord) -> list[str]:
    """Deterministic standout chips derived from the record (merged with the judge's)."""
    out: list[str] = []
    m = record.moat_signals
    if m.gov_contracts and m.gov_contract_value_aud:
        out.append(f"${m.gov_contract_value_aud:,} gov contracts")
    if m.gov_contract_agencies:
        out.append(f"{len(m.gov_contract_agencies)} gov agencies")
    if m.regulatory_accreditation:
        out.append("regulatory accreditation")
    if m.ip:
        out.append("IP holdings")
    if m.award_finalist:
        out.append("award finalist")
    if m.recurring_revenue_hint:
        out.append("recurring-revenue hint")
    return out
