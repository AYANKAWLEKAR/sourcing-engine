"""RankedCompany — a scored shortlist entry (next-phase plan §3.4)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .company import CompanyRecord


class RankedCompany(BaseModel):
    """One entry in the ranked shortlist.

    Carries the full record plus the two scores, the judge's rationale, the
    standout qualitative signals (which do NOT feed the statistical score but
    inform the judge + the analyst card), and the open diligence questions.
    """

    record: CompanyRecord
    s_stat: float                       # statistical fit, 0–100
    s_evidence: float = 0.0             # deterministic enriched-evidence score, 0–1
    s_final: float                      # blended final score, 0–1
    judge_fit: float | None = None      # the judge's calibrated 0–1 fit
    judge_rationale: str = ""
    standout_signals: list[str] = Field(default_factory=list)
    deferred_assessment: list[str] = Field(default_factory=list)
    # Fix 11: True when the LLM judge returned unparseable output.  The s_final
    # blend still runs (fit defaults to 0.0) but analysts should treat the judge
    # column as unverified and not rely on the rationale string.
    judge_unavailable: bool = False

    @property
    def rank_score(self) -> float:
        return self.s_final
