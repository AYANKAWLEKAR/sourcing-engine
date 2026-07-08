"""Operating-entity heuristics — tell a trading business from a holding/investment
shell so the ranker can demote non-operating entities.

Motivation (live HVAC/QLD run): investment-holding shells ("BENGUERRA INVESTMENTS
PTY LTD", "WILLANE INVESTMENTS PTY LTD") reached the top-12 because they carry the
right Maps category + state + model, and the locked statistical model scores only
those. The LLM judge caught them, but it was the *only* guard. This adds a cheap
structural/name signal that demotes shells through the existing
``unverified_gate_count`` penalty (no change to the locked score formula) and gives
the judge an explicit flag.

Deliberately conservative and SOFT: a match appends ``unverified:operating_entity``
(a demote + a diligence checklist item), never a hard EXCLUDE — some real SMEs use
holding structures, and the judge gets the final say.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

OPERATING_ENTITY_FLAG = "unverified:operating_entity"

# Strong shell/vehicle name markers only — NOT "group"/"enterprises"/"distributors"
# (those are ordinary operating businesses; a wrong business model is the judge's
# job, not this heuristic's).
_SHELL_NAME_RE = re.compile(
    r"\b(investments?|holdings?|nominees?|superannuation|super\s+fund|"
    r"family\s+trust|as\s+trustee|a\.?t\.?f\.?)\b",
    re.I,
)

# structure_guess values (from connectors/abn/parser.py::_STRUCTURE_MAP) that name
# a non-operating vehicle rather than a trading business.
_NON_OPERATING_STRUCTURES = frozenset({"trust", "government", "association"})


def is_non_operating(record: CompanyRecord) -> bool:
    """True when a record looks like a holding/investment vehicle, not a trading SME.

    Two conservative signals:
      1. ``ownership.structure_guess`` is a non-operating structure (trust/gov/assoc).
      2. A shell-style legal name AND no corroborating operating evidence — i.e. no
         website keyword hits. (Maps categories alone don't clear it: a holding co
         can trade under an operating name Maps categorised.)
    """
    struct = (record.ownership.structure_guess or "").strip().lower()
    if struct in _NON_OPERATING_STRUCTURES:
        return True
    if _SHELL_NAME_RE.search(record.legal_name or "") and not record.sector.keyword_hits:
        return True
    return False


def flag_operating_entity(record: CompanyRecord) -> bool:
    """Append the soft operating-entity flag if the record looks non-operating.

    Idempotent. Returns True if the record is flagged (already or now). The flag's
    ``unverified:`` prefix means it demotes via the existing scorer penalty and
    surfaces as a diligence checklist item — no change to the locked score formula.
    """
    if OPERATING_ENTITY_FLAG in record.flags:
        return True
    if is_non_operating(record):
        record.flags.append(OPERATING_ENTITY_FLAG)
        return True
    return False


def shortlist_quality_metrics(shortlist: list, resolved: list) -> dict:
    """Defensibility metrics for a run — used by the eval harness and to compare
    before/after a quality change (so improvements are measured, not assumed).

    ``shortlist``: list of RankedCompany (has ``.record`` + ``.judge_fit``).
    ``resolved``: list of CompanyRecord (the resolved pool the shortlist came from).
    """
    n_short = len(shortlist) or 1
    n_res = len(resolved) or 1
    shells = sum(1 for rc in shortlist if OPERATING_ENTITY_FLAG in rc.record.flags)
    fits = [rc.judge_fit for rc in shortlist if getattr(rc, "judge_fit", None) is not None]
    return {
        "shortlist_size": len(shortlist),
        "shell_rate_topN": round(shells / n_short, 3),
        "mean_judge_fit": round(sum(fits) / len(fits), 3) if fits else 0.0,
        "sector_signal_coverage": round(
            sum(1 for r in resolved if r.sector.keyword_hits) / n_res, 3
        ),
        "resolution_uncertain_rate": round(
            sum(1 for r in resolved if "abn_match_uncertain" in r.flags) / n_res, 3
        ),
        "pe_vc_unknown_rate": round(
            sum(1 for r in resolved if r.ownership.pe_vc_backed is None) / n_res, 3
        ),
    }
