"""GoogleMapsConnector — primary off-market discovery source (plan §5.1).

Wraps the Apify ``compass/crawler-google-places`` actor. Discovers businesses by
category + location (NO ABN — that is the EntityResolver's job) and maps each place
to a ``CompanyRecord`` carrying its category, geo, contacts, and review count.

Field names below were reconciled against real actor output (plan §4 guardrail):
the actor returns structured ``state`` / ``postalCode`` / ``city`` fields (and a
sometimes-null formatted ``address`` we fall back to parsing).
"""
from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "google_maps"

_AU_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "NT", "ACT", "TAS")
# Trailing "... QLD 4000" or "QLD 4000, Australia" in a formatted address.
_STATE_PC_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b\s*(\d{4})?")


class GoogleMapsConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "compass/crawler-google-places"
    cache_ttl_seconds: int = 30 * 24 * 3600  # plan §5.1: 30-day TTL

    def build_input(self, params: dict) -> dict:
        terms = params.get("search_terms") or params.get("searchStringsArray") or []
        if isinstance(terms, str):
            terms = [terms]
        terms = list(terms)
        per_search = int(params.get("max_places", 50))
        # Total ceiling across ALL search terms. maxCrawledPlacesPerSearch alone
        # caps per term, so N terms crawl N×per_search — this bounds the whole run.
        total_cap = per_search * max(1, len(terms))
        return {
            "searchStringsArray": terms,
            "locationQuery": params.get("location", ""),
            "maxCrawledPlacesPerSearch": per_search,
            "maxCrawledPlaces": total_cap,
            "language": "en",
            "countryCode": "au",  # actor requires lowercase ISO-3166
            "scrapeContacts": False,
        }

    def normalize(self, raw: dict) -> CompanyRecord:
        from ..models.company import (
            CompanyRecord,
            Location,
            Provenance,
            Sector,
            Size,
        )

        place_id = raw.get("placeId") or raw.get("cid") or raw.get("fid")
        if not place_id:
            # Fix 14: fall back to a content-hash rather than the bare title so two
            # businesses with identical names don't collide on the same entity_id.
            stable_key = f"{raw.get('title', '')}-{raw.get('postalCode', '')}-{raw.get('state', '')}"
            place_id = "hash:" + hashlib.sha1(stable_key.encode()).hexdigest()[:12]
        entity_id = f"maps:{place_id}"

        # Category text: primary category first, then the full list (deduped).
        cats: list[str] = []
        if raw.get("categoryName"):
            cats.append(raw["categoryName"])
        for c in raw.get("categories") or []:
            if c not in cats:
                cats.append(c)

        state, postcode = _resolve_state_postcode(raw)
        suburb = raw.get("city") or raw.get("neighborhood") or None
        loc = raw.get("location") or {}

        contacts: dict[str, Any] = {}
        if raw.get("website"):
            contacts["website"] = raw["website"]
        if raw.get("phone") or raw.get("phoneUnformatted"):
            contacts["phone"] = raw.get("phoneUnformatted") or raw.get("phone")

        fetched_at = raw.get("scrapedAt", "")

        def prov(field: str, confidence: float) -> Provenance:
            return Provenance(field=field, source=SOURCE_ID, fetched_at=fetched_at, confidence=confidence)

        provenance = [prov("category_text", 0.70)]
        if loc.get("lat") is not None:
            provenance.append(prov("location", 0.85))

        return CompanyRecord(
            entity_id=entity_id,
            abn=None,  # scrape layer never produces an ABN — resolver's job
            legal_name=raw.get("title"),
            country="Australia",
            location=Location(
                state=state,
                postcode=postcode,
                suburb=suburb,
                lat=loc.get("lat"),
                lng=loc.get("lng"),
            ),
            sector=Sector(category_text=cats),
            size=Size(review_count=raw.get("reviewsCount")),
            contacts_min=contacts,
            provenance=provenance,
        )


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _resolve_state_postcode(raw: dict) -> tuple[str | None, str | None]:
    """Prefer the actor's structured fields; fall back to parsing the address."""
    state = (raw.get("state") or "").strip().upper() or None
    postcode = (raw.get("postalCode") or "").strip() or None
    if state in _AU_STATES and postcode:
        return state, postcode

    # Fallback: parse "…, Brisbane QLD 4000, Australia" from the formatted address.
    address = raw.get("address") or ""
    m = _STATE_PC_RE.search(address)
    if m:
        state = state or m.group(1)
        postcode = postcode or m.group(2)
    return (state if state in _AU_STATES else None), postcode
