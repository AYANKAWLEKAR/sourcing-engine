"""YellowPagesConnector — directory discovery source (plan §5.2).

Apify ``abotapi/yellow-pages-au-scraper``. Same shape as Google Maps: name +
category + contacts, NO ABN. Cache TTL 7 days.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "yellow_pages"

_AU_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "NT", "ACT", "TAS")
_STATE_PC_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b\s*(\d{4})?")


class YellowPagesConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "abotapi/yellow-pages-au-scraper"
    cache_ttl_seconds: int = 7 * 24 * 3600

    def build_input(self, params: dict) -> dict:
        return {
            "keyword": params.get("keyword") or params.get("search_terms", ""),
            "location": params.get("location", ""),
            "maxItems": int(params.get("max_places", params.get("maxItems", 50))),
        }

    def normalize(self, raw: dict) -> CompanyRecord:
        from ..models.company import CompanyRecord, Location, Provenance, Sector

        name = raw.get("name") or raw.get("title")
        entity_id = f"yp:{raw.get('id') or raw.get('url') or name}"

        category = raw.get("category") or raw.get("categories")
        cats = [category] if isinstance(category, str) else list(category or [])

        address = raw.get("address") or ""
        m = _STATE_PC_RE.search(address)
        state = (raw.get("state") or (m.group(1) if m else None)) or None
        postcode = (raw.get("postcode") or (m.group(2) if m else None)) or None

        contacts = {}
        if raw.get("website"):
            contacts["website"] = raw["website"]
        if raw.get("phone"):
            contacts["phone"] = raw["phone"]

        return CompanyRecord(
            entity_id=entity_id,
            abn=None,
            legal_name=name,
            country="Australia",
            location=Location(state=state if state in _AU_STATES else None, postcode=postcode),
            sector=Sector(category_text=cats),
            contacts_min=contacts,
            provenance=[Provenance(field="category_text", source=SOURCE_ID, confidence=0.65)],
        )
