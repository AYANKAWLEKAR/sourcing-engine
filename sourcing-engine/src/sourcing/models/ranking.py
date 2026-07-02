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
    s_final: float                      # blended final score, 0–1
    judge_fit: float | None = None      # the judge's calibrated 0–1 fit
    judge_rationale: str = ""
    standout_signals: list[str] = Field(default_factory=list)
    deferred_assessment: list[str] = Field(default_factory=list)

    @property
    def rank_score(self) -> float:
        return self.s_final
