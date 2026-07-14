"""Statistical fit — the simplified, locked scoring model (next-phase plan §3.2).

    s_sector = 0.5·s_sem + 0.3·s_kw + 0.2·s_code
    fit      = 0.50·s_sector + 0.25·s_state + 0.25·s_model
    adjusted = fit · (0.7 + 0.3·mean_confidence)            # confidence dampener
    score    = adjusted · (0.85 ** unverified_gate_count) · 100

Three SCORE fields only. NO s_ai / s_frag / s_size / s_age. NO proxy-flag penalty.
Moat signals and awards do NOT score in ``statistical_fit`` — they inform the judge,
the card, and the SEPARATE, fully-deterministic ``evidence_score`` (below), which the
blend in ``rank.py`` weights alongside S_stat and the judge's fit.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..rag.embeddings import get_embedding_provider
from ..rag.vector_store import cosine

if TYPE_CHECKING:
    from ..models.company import CompanyRecord
    from .buybox import BuyBox

_MIXED_MODEL_PARTIAL = 0.5
_UNVERIFIED_PENALTY = 0.85


def _embedder():
    return get_embedding_provider()


def s_sem(record: CompanyRecord, buybox: BuyBox, embedder=None) -> float:
    """Semantic similarity between the record's sector text and the buy-box query."""
    embedder = embedder or _embedder()
    rec_text = " ".join([*record.sector.category_text, *record.sector.keyword_hits]).strip()
    if not rec_text or not buybox.query_text().strip():
        return 0.0
    a = embedder.embed([rec_text])[0]
    b = embedder.embed([buybox.query_text()])[0]
    return max(0.0, min(1.0, cosine(a, b)))


def s_kw(record: CompanyRecord, buybox: BuyBox) -> float:
    if record.sector.keyword_density is not None:
        return max(0.0, min(1.0, record.sector.keyword_density))
    wanted = {k.lower() for k in buybox.sector_keywords}
    if not wanted:
        return 0.0
    hits = {h.lower() for h in record.sector.keyword_hits}
    return len(hits & wanted) / len(wanted)


def s_code(record: CompanyRecord, buybox: BuyBox) -> float:
    if not buybox.anzsic or not record.sector.anzsic:
        return 0.0
    wanted = {c[:4] for c in buybox.anzsic}
    have = {c[:4] for c in record.sector.anzsic}
    return 1.0 if (wanted & have) else 0.0


def s_state(record: CompanyRecord, buybox: BuyBox) -> float:
    if not buybox.states:
        return 1.0  # no geo constraint → neutral-pass
    return 1.0 if (record.location.state or "").upper() in buybox.states else 0.0


def s_model(record: CompanyRecord, buybox: BuyBox) -> float:
    if not buybox.target_models:
        return 1.0
    model = (record.business_model or "").upper()
    if model in buybox.target_models:
        return 1.0
    if model == "MIXED":
        return _MIXED_MODEL_PARTIAL
    return 0.0


def mean_confidence(record: CompanyRecord) -> float:
    confs = [p.confidence for p in record.provenance if p.confidence is not None]
    return sum(confs) / len(confs) if confs else 0.5


def unverified_gate_count(record: CompanyRecord) -> int:
    flags = [*record.flags, *record.screen.flags]
    return sum(1 for f in flags if f.startswith("unverified:"))


def statistical_fit(record: CompanyRecord, buybox: BuyBox, embedder=None) -> float:
    sector = 0.5 * s_sem(record, buybox, embedder) + 0.3 * s_kw(record, buybox) + 0.2 * s_code(record, buybox)
    fit = 0.50 * sector + 0.25 * s_state(record, buybox) + 0.25 * s_model(record, buybox)
    adjusted = fit * (0.7 + 0.3 * mean_confidence(record))            # confidence dampener
    return adjusted * (_UNVERIFIED_PENALTY ** unverified_gate_count(record)) * 100


# ---------------------------------------------------------------------------
# Evidence score — a SEPARATE deterministic layer over the enriched signals.
#
# Rewards the hard, data-backed evidence the analyst cares about most:
# government contracts/tenders (AusTender), award finalists/winners (Trades
# Champion / Telstra), IP (IPGOD), regulatory accreditation, and a verified
# EBITDA that lands in the buy-box band. Everything here is grounded in a
# concrete record field — no LLM, no hallucination — and financial terms are
# down-weighted by their own confidence so proxy estimates can't over-reward.
# ---------------------------------------------------------------------------

_GOV_VALUE_CAP = 5_000_000          # AUD at which the gov-contract term saturates to 1.0
_GOV_PRESENCE_FLOOR = 0.4           # credit for gov_contracts=True with unknown value

_EVIDENCE_WEIGHTS = {
    "gov": 0.30,
    "award": 0.20,
    "ip": 0.10,
    "accred": 0.10,
    "ebitda_fit": 0.30,
}


def s_gov(record: CompanyRecord) -> float:
    """Government-contract strength: log-scaled dollar value, or a floor if only presence is known."""
    m = record.moat_signals
    value = m.gov_contract_value_aud
    if value and value > 0:
        scaled = math.log10(1 + value) / math.log10(1 + _GOV_VALUE_CAP)
        return max(_GOV_PRESENCE_FLOOR, min(1.0, scaled))
    if m.gov_contracts:
        return _GOV_PRESENCE_FLOOR
    return 0.0


def s_award(record: CompanyRecord) -> float:
    """Best award signal: national winner 1.0, national finalist 0.8, regional 0.5."""
    best = 0.0
    for a in record.award_signals:
        if a.tier == 1:
            best = max(best, 1.0 if a.level == "winner" else 0.8)
        else:
            best = max(best, 0.6 if a.level == "winner" else 0.5)
    if record.moat_signals.award_finalist:
        best = max(best, 0.5)
    return best


def s_ip(record: CompanyRecord) -> float:
    m = record.moat_signals
    if not m.ip:
        return 0.0
    count = m.ip_count or 1
    return min(1.0, count / 3.0)


def s_accred(record: CompanyRecord) -> float:
    return 1.0 if record.moat_signals.regulatory_accreditation else 0.0


def s_ebitda_fit(record: CompanyRecord, buybox: BuyBox) -> float:
    """1.0 for a verified EBITDA inside the buy-box band, graded by distance outside it,
    then scaled by the estimate's own confidence so proxy figures count for less."""
    ebitda = record.size.ebitda_est_aud
    if ebitda is None:
        return 0.0
    lo, hi = buybox.ebitda_min, buybox.ebitda_max
    if lo is None and hi is None:
        base = 1.0  # no band to hit → any known EBITDA is a mild positive
    elif (lo is None or ebitda >= lo) and (hi is None or ebitda <= hi):
        base = 1.0
    else:
        # Graded falloff: how far outside the band, relative to the band width.
        width = (hi - lo) if (lo is not None and hi is not None and hi > lo) else max(abs(ebitda), 1.0)
        if lo is not None and ebitda < lo:
            dist = lo - ebitda
        else:
            dist = ebitda - hi  # hi is not None here
        base = max(0.0, 1.0 - dist / width)
    conf = record.size.ebitda_confidence
    conf = 0.5 if conf is None else max(0.0, min(1.0, conf))
    return base * conf


def evidence_score(record: CompanyRecord, buybox: BuyBox) -> float:
    """Deterministic 0–1 blend of the enriched evidence terms."""
    w = _EVIDENCE_WEIGHTS
    total = (
        w["gov"] * s_gov(record)
        + w["award"] * s_award(record)
        + w["ip"] * s_ip(record)
        + w["accred"] * s_accred(record)
        + w["ebitda_fit"] * s_ebitda_fit(record, buybox)
    )
    return max(0.0, min(1.0, total))
