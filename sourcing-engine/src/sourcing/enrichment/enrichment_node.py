"""EnrichmentNode — the per-record waterfall over the resolved pool (plan §2.3 + addendum §5).

Order per record (cheap/free first):
  1. AusTender — free direct-ABN gov-contract signal (full-pool sweep)
  2. Website text → SignalExtractor (qwen) — sector, model, ANZSIC, moat
LinkedIn + proxy estimator stay deferred behind the shortlist gate (not here).

Every component is injectable so the node unit-tests offline with fakes; nothing
is fabricated — unfillable fields get an ``unverified:*`` flag with a reason.
"""
from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
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

    def enrich_pool(
        self,
        pool: list[CompanyRecord],
        buybox: BuyBox,
        *,
        max_workers: int | None = None,
        checkpoint: Callable[[CompanyRecord], None] | None = None,
    ) -> list[CompanyRecord]:
        """Enrich a pool of resolved records.

        Fix 15: ``max_workers > 1`` runs enrichment concurrently via a
        ``ThreadPoolExecutor`` (the rate limiter inside each APIConnector is
        now thread-safe — Fix 16).

        Fix 18: ``checkpoint`` is an optional ``Callable[[CompanyRecord], None]``
        called after each record is enriched (e.g. write to the companies table).
        If enrichment crashes partway through the pool the already-enriched records
        are not lost.
        """
        records = [r for r in pool if r.abn]

        if max_workers and max_workers > 1:
            def _work(rec: CompanyRecord) -> CompanyRecord:
                self.enrich_one(rec, buybox)
                if checkpoint is not None:
                    checkpoint(rec)
                return rec

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                list(ex.map(_work, records))
        else:
            for rec in records:
                self.enrich_one(rec, buybox)
                if checkpoint is not None:
                    checkpoint(rec)

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
                    # Fix 10: call normalize() to honour the SourceConnector contract
                    # rather than reading raw Apify keys directly.  WebsiteFetchConnector
                    # stores text in deferred_assessment["website_text_raw"].
                    first = items[0]
                    normalized = self.website.normalize(first)
                    rec.website_text_raw = (
                        normalized.website_text_raw
                        or normalized.deferred_assessment.get("website_text_raw")
                        or ""
                    )
            except Exception:
                rec.flags.append("unverified:sector:website_fetch_failed")

        self.signal_extractor.extract(rec, buybox)
        return rec
