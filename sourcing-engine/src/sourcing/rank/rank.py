"""Rank pipeline — screen → score → judge → blend → diversity (plan §3.3–3.4).

    S_final = 0.40·(S_stat/100) + 0.25·judge_fit + 0.35·S_ev

S_stat is sector/geo/model fit; judge_fit is the LLM's qualitative read; S_ev is the
deterministic enriched-evidence score (gov contracts, awards, IP, accreditation,
EBITDA-in-band). The top judge_k by S_stat go to the judge; a diversity guard stops
the top-k from collapsing onto one postcode. Returns a list of RankedCompany.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.ranking import RankedCompany
from .judge import LLMJudge, standout_signals
from .score import evidence_score, statistical_fit
from .screen import screen

if TYPE_CHECKING:
    from ..models.company import CompanyRecord
    from .buybox import BuyBox

_STAT_WEIGHT = 0.40
_JUDGE_WEIGHT = 0.25
_EVIDENCE_WEIGHT = 0.35
_DEFAULT_POSTCODE_CAP = 3


# Friendly wording for known unverified flags (raw flag → analyst-readable item).
_FRIENDLY_FLAG = {
    "unverified:operating_entity": "confirm this is an operating business (not a holding/investment vehicle)",
}


def deferred_items(record: CompanyRecord) -> list[str]:
    """Open diligence questions: the unverified fields + standard checks.

    Public so the shortlist gate can rebuild a RankedCompany's checklist after
    late enrichment (headcount/proxy estimates) changes the record.
    """
    items = [
        _FRIENDLY_FLAG.get(f, f)
        for f in (*record.flags, *record.screen.flags)
        if f.startswith("unverified:")
    ]
    # Honesty: PE/VC backing is an EXCLUDE criterion, but until a source fills it
    # (Inven), ``None`` means "not checked" — surface it rather than passing silently.
    if record.ownership.pe_vc_backed is None:
        items.append("verify ownership — PE/VC backing not checked (no source filled it)")
    if record.size.ebitda_est_aud is None:
        items.append("verify EBITDA / financials (no estimate yet)")
    if "austender_checked_no_contracts" in record.flags:
        items.append("confirm no government-revenue dependency")
    if record.resolution_confidence is not None and record.resolution_confidence < 0.85:
        items.append(f"confirm ABN match (resolution_confidence={record.resolution_confidence:.2f})")
    # Dedup while preserving order.
    seen: set[str] = set()
    return [i for i in items if not (i in seen or seen.add(i))]


def rank_pool(
    pool: list[CompanyRecord],
    buybox: BuyBox,
    *,
    judge: LLMJudge | None = None,
    embedder=None,
    top_k: int = 20,
    judge_k: int = 50,
    postcode_cap: int = _DEFAULT_POSTCODE_CAP,
) -> list[RankedCompany]:
    judge = judge or LLMJudge()

    # 1. SCREEN
    survivors = [r for r in pool if screen(r, buybox)]

    # 2. SCORE (statistical) + sort
    scored = sorted(
        ((r, statistical_fit(r, buybox, embedder)) for r in survivors),
        key=lambda t: t[1],
        reverse=True,
    )

    # 3. JUDGE the top judge_k, blend into S_final
    ranked: list[RankedCompany] = []
    for record, s_stat in scored[:judge_k]:
        jr = judge.judge(record, buybox)
        s_ev = evidence_score(record, buybox)
        s_final = (
            _STAT_WEIGHT * (s_stat / 100.0)
            + _JUDGE_WEIGHT * jr.fit
            + _EVIDENCE_WEIGHT * s_ev
        )
        # Standout chips must be GROUNDED in the record — the judge's free-text
        # signals can hallucinate facts (e.g. invent a gov-contract figure), so we
        # use only the deterministic, data-backed signals. The judge's qualitative
        # read lives in the rationale.
        signals = standout_signals(record)
        ranked.append(
            RankedCompany(
                record=record,
                s_stat=round(s_stat, 2),
                s_evidence=round(s_ev, 4),
                s_final=round(s_final, 4),
                judge_fit=round(jr.fit, 3),
                judge_rationale=jr.rationale,
                standout_signals=signals,
                deferred_assessment=deferred_items(record),
                judge_unavailable=jr.unavailable,
            )
        )

    ranked.sort(key=lambda rc: rc.s_final, reverse=True)

    # 4. DIVERSITY GUARD — cap per postcode so the top-k doesn't collapse on one area
    return _diversify(ranked, top_k, postcode_cap)


def _diversify(ranked: list[RankedCompany], top_k: int, postcode_cap: int) -> list[RankedCompany]:
    selected: list[RankedCompany] = []
    counts: dict[str, int] = {}
    overflow: list[RankedCompany] = []

    for rc in ranked:
        pc = rc.record.location.postcode or "?"
        if counts.get(pc, 0) < postcode_cap:
            selected.append(rc)
            counts[pc] = counts.get(pc, 0) + 1
        else:
            overflow.append(rc)
        if len(selected) >= top_k:
            return selected

    # Backfill from overflow if the cap left us short.
    for rc in overflow:
        if len(selected) >= top_k:
            break
        selected.append(rc)
    return selected
