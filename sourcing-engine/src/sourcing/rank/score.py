"""Statistical fit — the simplified, locked scoring model (next-phase plan §3.2).

    s_sector = 0.5·s_sem + 0.3·s_kw + 0.2·s_code
    fit      = 0.50·s_sector + 0.25·s_state + 0.25·s_model
    adjusted = fit · (0.7 + 0.3·mean_confidence)            # confidence dampener
    score    = adjusted · (0.85 ** unverified_gate_count) · 100

Three SCORE fields only. NO s_ai / s_frag / s_size / s_age. NO proxy-flag penalty.
Moat signals and awards do NOT score here — they inform the judge and the card.
"""
from __future__ import annotations

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
