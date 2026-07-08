"""Screening sequence — EXCLUDE → GATE → PROXY_GATE (next-phase plan §3.1).

``screen(record, buybox)`` returns True for survivors that proceed to SCORE.
Only KNOWN violations fail a record; unknown gate fields pass but are flagged
``unverified:*`` (the scorer applies the unverified-gate penalty, per the model).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.company import CompanyRecord
    from .buybox import BuyBox

# Proxy band tolerance — the estimate is low-confidence, so only fail well outside.
_PROXY_LOW = 0.5
_PROXY_HIGH = 1.5


def hits_any_exclude(record: CompanyRecord, buybox: BuyBox) -> bool:
    if buybox.exclude_listed and record.ownership.listed_entity is True:
        record.screen.flags.append("exclude:listed_entity")
        return True
    if buybox.exclude_pe_vc and record.ownership.pe_vc_backed is True:
        record.screen.flags.append("exclude:pe_vc_backed")
        return True
    if record.sector.exclude_hits:
        record.screen.flags.append("exclude:sector")
        return True
    return False


def fails_any_gate(record: CompanyRecord, buybox: BuyBox) -> bool:
    if record.country and record.country.lower() != "australia":
        record.screen.flags.append("gate:country")
        return True
    yrs = record.age.years_operating
    if buybox.min_years is not None:
        if yrs is None:
            record.flags.append("unverified:years_operating")
        elif yrs < buybox.min_years:
            record.screen.flags.append("gate:years_operating")
            return True
    return False


def fails_proxy_gate_beyond_error(record: CompanyRecord, buybox: BuyBox) -> bool:
    est = record.size.ebitda_est_aud
    if est is None:
        return False  # no proxy estimate (full-sweep default) → don't gate
    if buybox.ebitda_max is not None and est > buybox.ebitda_max * _PROXY_HIGH:
        record.screen.flags.append("proxy_gate:ebitda_high")
        return True
    if buybox.ebitda_min is not None and est < buybox.ebitda_min * _PROXY_LOW:
        record.screen.flags.append("proxy_gate:ebitda_low")
        return True
    return False


def screen(record: CompanyRecord, buybox: BuyBox) -> bool:
    if hits_any_exclude(record, buybox):
        record.screen.status = "excluded"
        return False
    if fails_any_gate(record, buybox):
        record.screen.status = "gated_out"
        return False
    if fails_proxy_gate_beyond_error(record, buybox):
        record.screen.status = "proxy_gated_out"
        return False
    # Soft signals — flag but never fail. A suspected holding/investment shell is
    # demoted via the scorer's unverified-gate penalty and surfaced to the judge +
    # diligence checklist, but a survivor (real SMEs sometimes use these structures).
    from .quality import flag_operating_entity

    flag_operating_entity(record)
    record.screen.status = "survivor"
    return True
