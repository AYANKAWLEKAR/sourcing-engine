"""EnrichmentNode — the per-record waterfall over the resolved pool (plan §2.3 + addendum §5).

Order per record (cheap/free first):
  1. AusTender — free direct-ABN gov-contract signal (full-pool sweep)
  2. Website text → SignalExtractor (qwen) — sector, model, ANZSIC, moat
LinkedIn + proxy estimator stay deferred behind the shortlist gate (not here).

Every component is injectable so the node unit-tests offline with fakes; nothing
is fabricated — unfillable fields get an ``unverified:*`` flag with a reason.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .signal_extractor import SignalExtractor

if TYPE_CHECKING:
    from ..models.company import CompanyRecord
    from ..rank.buybox import BuyBox


class EnrichmentNode:
    def __init__(
        self,
        austender: Any = None,
        website: Any = None,
        signal_extractor: SignalExtractor | None = None,
    ):
        # Lazy defaults so importing needs no credentials; callers inject fakes in tests.
        if austender is None:
            from ..connectors.austender import AusTenderConnector

            austender = AusTenderConnector()
        self.austender = austender
        self.website = website  # None → skip live text fetch (use existing text)
        self.signal_extractor = signal_extractor or SignalExtractor()

    def enrich_pool(self, pool: list[CompanyRecord], buybox: BuyBox) -> list[CompanyRecord]:
        for rec in pool:
            if not rec.abn:
                continue  # only enrich resolved records
            self.enrich_one(rec, buybox)
        return pool

    def enrich_one(self, rec: CompanyRecord, buybox: BuyBox) -> CompanyRecord:
        # 1. AusTender — cheap, free, direct ABN join
        self.austender.enrich_record(rec)

        # 2. Website text → signal extraction (qwen)
        website = rec.contacts_min.get("website")
        if website and not rec.website_text_raw and self.website is not None:
            try:
                items = self.website.fetch({"url": website})
                if items:
                    first = items[0]
                    rec.website_text_raw = first.get("markdown") or first.get("text") or ""
            except Exception:
                rec.flags.append("unverified:sector:website_fetch_failed")

        self.signal_extractor.extract(rec, buybox)
        return rec
