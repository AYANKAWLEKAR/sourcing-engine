"""SignalExtractor — website text → ranked signals (next-phase plan §2.1).

Classifies ``website_text_raw`` into the sector/model/moat fields the ranker
scores. Uses a **local Ollama model (qwen by default)** in JSON mode — NOT a
cloud API. The LLM client is injectable so unit tests run offline with a fake.

Never fabricates: empty/short text → an ``unverified:sector:no_website_text``
flag and an untouched record.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import get_settings
from ..llm import complete_json, get_llm_client

if TYPE_CHECKING:
    from ..llm import LLMClient
    from ..models.company import CompanyRecord
    from ..rank.buybox import BuyBox

_SYSTEM = (
    "You classify Australian companies from their website text for an M&A buy box. "
    "Respond with ONLY a single JSON object, no prose."
)

_PROMPT = """Classify this Australian company from its website text.
Include keywords (sector fit): {include}
Exclude keywords (sector anti-fit): {exclude}

Website text (truncated):
{text}

For business_model choose exactly ONE value: B2B (sells to businesses/government),
B2C (sells to consumers), MIXED, or UNKNOWN. Most trade/industrial service firms are B2B.

Return ONLY this JSON shape (fill in real values):
{{"keyword_hits": ["matched include keywords"], "exclude_hits": ["matched exclude keywords"],
  "keyword_density": 0.0,
  "business_model": "B2B",
  "moat_signals": {{"physical_ops": false, "regulatory_accreditation": false,
                    "hard_assets": false, "recurring_revenue_hint": false}},
  "anzsic_guess": "4-digit code or null", "anzsic_confidence": 0.0}}"""

_VALID_MODELS = {"B2B", "B2C", "MIXED", "UNKNOWN"}


class SignalExtractor:
    def __init__(self, llm: LLMClient | None = None, model: str | None = None):
        self._llm = llm or get_llm_client()
        self._model = model or get_settings().enrich_model

    def extract(self, record: CompanyRecord, buybox: BuyBox) -> CompanyRecord:
        from ..models.company import Provenance

        text = (record.website_text_raw or "").strip()
        if len(text) < 40:  # nothing meaningful to classify
            record.flags.append("unverified:sector:no_website_text")
            return record

        data = complete_json(
            self._llm,
            self._model,
            _SYSTEM,
            _PROMPT.format(
                include=", ".join(buybox.sector_keywords) or "(none)",
                exclude=", ".join(buybox.sector_exclude_keywords) or "(none)",
                text=text[:4000],
            ),
        )
        if not data:
            record.flags.append("unverified:sector:extract_failed")
            return record

        record.sector.keyword_hits = _as_list(data.get("keyword_hits"))
        record.sector.exclude_hits = _as_list(data.get("exclude_hits"))
        record.sector.keyword_density = _as_float(data.get("keyword_density"))

        model = str(data.get("business_model", "")).upper()
        record.business_model = model if model in _VALID_MODELS else "UNKNOWN"

        anzsic = data.get("anzsic_guess")
        if anzsic and str(anzsic).lower() != "null":
            record.sector.anzsic = [str(anzsic)]
            record.sector.anzsic_confidence = _as_float(data.get("anzsic_confidence"))

        moat = data.get("moat_signals") or {}
        for key in ("physical_ops", "regulatory_accreditation", "hard_assets", "recurring_revenue_hint"):
            if key in moat:
                setattr(record.moat_signals, key, bool(moat[key]))

        record.provenance.append(
            Provenance(field="sector", source="signal_extractor", confidence=0.70)
        )
        record.provenance.append(
            Provenance(field="business_model", source="signal_extractor", confidence=0.70)
        )
        return record


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
