"""EnrichmentNode — the per-record waterfall over the resolved pool (plan §2.3 + addendum §5).

Order per record (cheap/free first):
  0. ASX listed check + IPGOD IP moat — free local DuckDB lookups
  1. AusTender — free direct-ABN gov-contract signal (full-pool sweep)
  2. Website text → SignalExtractor (qwen) — sector, model, ANZSIC, moat
LinkedIn + proxy estimator stay deferred behind the shortlist gate (not here).

Every component is injectable so the node unit-tests offline with fakes; nothing
is fabricated — unfillable fields get an ``unverified:*`` flag with a reason.
``ipgod``/``asx`` default to None (skipped) so constructing the node never
touches bulk data; production wires them in ``runs/pipeline.build_default``,
gated on their settings paths resolving.
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
        ipgod: Any = None,
        asx: Any = None,
        record_cache: Any = None,
        nata_cache: Any = None,
    ):
        # Lazy defaults so importing needs no credentials; callers inject fakes in tests.
        if austender is None:
            from ..connectors.austender import AusTenderConnector

            austender = AusTenderConnector()
        self.austender = austender
        self.website = website  # None → skip live text fetch (use existing text)
        self.signal_extractor = signal_extractor or SignalExtractor()
        self.ipgod = ipgod  # None → skip IP moat lookup
        self.asx = asx      # None → skip listed-entity check
        # None → no persistent record cache (external calls always run).
        self.record_cache = record_cache
        self.nata_cache = nata_cache  # None → skip Plan B NATA lookup

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
        # Cache hit: reuse a prior run's external-source enrichment for this ABN
        # (website text + IPGOD/AusTender/ASX signals) and skip the Apify website
        # fetch + the AusTender scan. Keyword extraction still re-runs below
        # against the cached text, so the current buy-box scores correctly.
        cache_hit = False
        if self.record_cache is not None and rec.abn:
            cached = self.record_cache.get(rec.abn)
            if cached is not None:
                from .record_cache import apply_cached_enrichment

                apply_cached_enrichment(rec, cached)
                rec.flags.append("enrichment_cache_hit")
                cache_hit = True

        if not cache_hit:
            # 0a. ASX listed check — free local lookup; makes the listed_entity
            #     EXCLUDE fire on an explicit match (never writes False on a miss).
            if self.asx is not None:
                try:
                    self.asx.enrich_record(rec)
                except Exception:
                    rec.flags.append("unverified:listed_entity:asx_lookup_failed")

            # 0b. IPGOD IP moat — free local direct-ABN lookup.
            if self.ipgod is not None:
                try:
                    self.ipgod.enrich_record(rec)
                except Exception:
                    rec.flags.append("unverified:ip:ipgod_lookup_failed")

            # 1. AusTender — cheap, free, direct ABN join
            self.austender.enrich_record(rec)

            # Plan B: annotate with NATA accreditation from the sweep cache.
            # Guarded + non-fatal: a missing table or query error is a silent no-op.
            if self.nata_cache is not None and rec.legal_name:
                try:
                    hit = self.nata_cache.find_by_normalized_name(
                        rec.legal_name, rec.location.state)
                    if hit:
                        m = rec.moat_signals
                        m.regulatory_accreditation = True
                        m.nata_accreditation = True
                        m.nata_site_count = hit["nata_site_count"]
                        m.nata_service_types = hit["nata_service_types"]
                        m.nata_accreditation_numbers = hit["nata_accreditation_numbers"]
                        m.nata_states = hit["nata_states"]
                        m.nata_multistate = hit["nata_multistate"]
                        from ..models.company import Provenance
                        rec.provenance.append(Provenance(
                            field="nata_accreditation", source="nata_cache", confidence=0.9))
                except Exception:
                    pass

            # 2. Website text (Apify) — the expensive external call the cache saves.
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

        # Signal extraction always runs (cheap, buy-box-specific; uses cached or
        # freshly fetched website text — no network).
        self.signal_extractor.extract(rec, buybox)

        # Persist the enriched record for reuse on the next run.
        if self.record_cache is not None and rec.abn and not cache_hit:
            self.record_cache.put(rec)
        return rec
