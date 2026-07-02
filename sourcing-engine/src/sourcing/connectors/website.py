"""WebsiteFetchConnector — page-text enrichment source (plan §5.2).

Apify ``apify/rag-web-browser``. Given a URL, returns the page as markdown and
attaches it as ``website_text_raw`` for the (later) signal extractor to classify.
Keyed by URL, not a discovery source. Cache TTL 14 days.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "website_fetch"


class WebsiteFetchConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "apify/rag-web-browser"
    cache_ttl_seconds: int = 14 * 24 * 3600

    def build_input(self, params: dict) -> dict:
        return {
            "query": params["url"],
            "maxResults": 1,
            "outputFormats": ["markdown"],
        }

    def normalize(self, raw: dict) -> CompanyRecord:
        from ..models.company import CompanyRecord, Provenance

        # rag-web-browser returns {metadata:{url,...}, markdown:"..."} (or text).
        meta = raw.get("metadata") or {}
        url = meta.get("url") or raw.get("url") or ""
        text = raw.get("markdown") or raw.get("text") or ""

        return CompanyRecord(
            entity_id=f"web:{url}",
            abn=None,
            legal_name=meta.get("title") or raw.get("title"),
            country="Australia",
            deferred_assessment={"website_text_raw": text},
            provenance=[Provenance(field="website_text_raw", source=SOURCE_ID, confidence=0.90)],
        )
