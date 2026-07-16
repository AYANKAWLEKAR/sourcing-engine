"""SourcingOrchestrator — the missing BuyBox → connector params → scrape layer
(audit Fixes 1, 2, 3).

Before this module, scraping parameters had to be hand-coded (as in
rank_demo.py DISCOVERY dict), completely decoupled from the BuyBox.  This is the
translation layer that was absent.

Two public entry points:

``params_for_connector(source_id, buybox, **kwargs) -> list[dict]``
    Derives ``fetch()`` param dicts from the BuyBox for a named source.
    Returns a *list* so multi-state buy-boxes produce per-state tiles for scrape
    connectors (Fix 2 — geographic tiling).

``SourcingOrchestrator(registry_entries).fetch_all(plan, buybox) -> list[CompanyRecord]``
    Iterates the SourcePlan, derives params, runs each connector, and aggregates
    raw candidates.  Uses the ConnectorRegistry singleton (Fix 17) to avoid
    re-instantiating stateful connectors on every call.

Fix 3 (ASIC state filter): the orchestrator never passes ``state`` to the ASIC
spine connector.  The ASIC ``previous_state`` column records the state of
*incorporation*, not *current operating state*.  Geo screening belongs in the
``s_state`` scorer (after resolution merges the ABN-spine operating state).
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models.company import CompanyRecord
    from .models.source import SourcePlanItem, SourceRegistryEntry
    from .rank.buybox import BuyBox

# ---------------------------------------------------------------------------
# State → geographic query string for scrape actor locationQuery / location.
# Using the state name (not city) widens coverage to regional businesses.
# ---------------------------------------------------------------------------
_STATE_LOCATION: dict[str, str] = {
    "NSW": "New South Wales Australia",
    "VIC": "Victoria Australia",
    "QLD": "Queensland Australia",
    "SA":  "South Australia",
    "WA":  "Western Australia",
    "TAS": "Tasmania Australia",
    "NT":  "Northern Territory Australia",
    "ACT": "Australian Capital Territory Australia",
}

# Sources that require per-state geographic tiling (Fix 2).
_TILED_SOURCES: frozenset[str] = frozenset({
    "google_maps", "yellow_pages", "industrynet", "retail_pos_directory",
})

# Spine sources that must NOT receive a state filter (Fix 3).
# Their state column is registration state, not operating state.
_SPINE_SOURCES: frozenset[str] = frozenset({
    "asic_company_dataset", "abn_bulk_extract",
})

# Sources that enrich/resolve/screen EXISTING records (by ABN/URL) or return a
# whole reference table — they do NOT discover new candidates from buy-box
# params, so they must never run in the discovery sweep. ``abn_bulk_extract`` is
# here too: it is a resolver fallback (targeted lookup_abn via EntityResolver),
# never a discovery sweep — sweeping it would load the whole ~20M-row register.
_ENRICHMENT_SOURCES: frozenset[str] = frozenset({
    "austender", "website_fetch", "ipgod", "asx_listed_list", "abn_bulk_extract",
})

_DEFAULT_MAX_PLACES = 50


# ---------------------------------------------------------------------------
# params_for_connector
# ---------------------------------------------------------------------------

def params_for_connector(
    source_id: str,
    buybox: BuyBox,
    *,
    max_places: int = _DEFAULT_MAX_PLACES,
    min_years: int | None = None,
    entity_types: list[str] | None = None,
    spine_limit: int = 2000,
) -> list[dict]:
    """Derive ``fetch()`` param dicts from ``buybox`` for the named source.

    Returns a list because scrape connectors are tiled per state (Fix 2):
    each element maps to one ``connector.fetch()`` call.  Spine/API connectors
    always return a single-element list.

    Fix 3: spine connectors (ASIC, ABN bulk) receive NO ``state`` parameter
    here.  Pass ``state`` explicitly if you specifically need registration-state
    filtering (e.g. for a data audit), but understand it is NOT operating state.
    """
    keywords: list[str] = buybox.sector_keywords or []
    states: list[str] = buybox.states or []

    # --- NATA: tile per state x include-keyword (its own param shape) --------
    if source_id == "nata_accreditation":
        from .config import get_settings

        max_terms = get_settings().scrape_max_search_terms
        terms = (keywords or ["testing"])[:max_terms]
        target_states = states or ["NSW", "VIC", "QLD", "SA", "WA"]
        return [
            {"state": st, "search": kw, "filter_by": "service", "status": ""}
            for st in target_states
            for kw in terms
        ]

    # --- scrape connectors: tile per state ----------------------------------
    if source_id in _TILED_SOURCES:
        from .config import get_settings

        locations = [
            _STATE_LOCATION.get(s, f"{s} Australia") for s in states
        ] or ["Australia"]
        tiles: list[dict] = []
        # Cap search terms: Google Maps crawls up to max_places PER term, so a
        # 20-keyword buy-box otherwise fans out into a huge, slow scrape.
        max_terms = get_settings().scrape_max_search_terms
        search_terms = (keywords or ["business"])[:max_terms]
        primary_kw = keywords[0] if keywords else "business"
        for loc in locations:
            tiles.append({
                # GoogleMapsConnector keys
                "search_terms": search_terms,
                "searchStringsArray": search_terms,
                "location": loc,
                "locationQuery": loc,
                "max_places": max_places,
                # YellowPagesConnector keys
                "keyword": primary_kw,
                "maxItems": max_places,
            })
        return tiles

    # --- spine sources: structural filters only (NO state) ------------------
    if source_id in _SPINE_SOURCES:
        p: dict[str, Any] = {"limit": spine_limit}
        eff_min_years = min_years if min_years is not None else buybox.min_years
        if eff_min_years:
            p["min_years"] = eff_min_years
        if entity_types:
            p["entity_types"] = entity_types
        return [p]

    # --- ABN Lookup API: name-search per keyword × state --------------------
    if source_id == "abn_lookup_api":
        if not keywords and not states:
            return [{}]
        tiles = []
        for kw in (keywords or ["business"]):
            for state in (states or [None]):
                p = {"name": kw}
                if state:
                    p["state"] = state
                tiles.append(p)
        return tiles

    # --- enrichment sources (AusTender, website, IPGOD, ASX): no BuyBox params
    # (these are skipped by fetch_all's discovery sweep; params kept for any
    # direct/targeted call). abn_bulk_extract is handled by the spine branch.
    if source_id in _ENRICHMENT_SOURCES:
        return [{}]

    # --- award registers: pass target states --------------------------------
    if source_id in {"telstra_awards", "local_business_awards", "trades_champion"}:
        return [{"categories": [], "states": list(states)}]

    # --- generic fallback ---------------------------------------------------
    return [{"keywords": keywords, "states": states, "max_results": max_places}]


# ---------------------------------------------------------------------------
# SourcingOrchestrator
# ---------------------------------------------------------------------------

class SourcingOrchestrator:
    """Runs a SourcePlan against a BuyBox and returns a raw (un-resolved) pool.

    Needs the full ``SourceRegistryEntry`` list (not just ``SourcePlanItem``)
    because ``SourcePlanItem`` does not carry ``connector_ref`` or ``gate``.

    Example::

        entries = load_seed_registry()
        plan    = retriever.retrieve(ruleset)
        pool    = SourcingOrchestrator(entries).fetch_all(plan, buybox)
        # pool is list[CompanyRecord] — scrape records without ABN yet
    """

    def __init__(
        self,
        registry_entries: list[SourceRegistryEntry],
        *,
        connector_registry: Any = None,
    ) -> None:
        self._entries: dict[str, SourceRegistryEntry] = {
            e.source_id: e for e in registry_entries
        }
        if connector_registry is None:
            from .connectors.connector_registry import ConnectorRegistry
            connector_registry = ConnectorRegistry.get()
        self._conn_registry = connector_registry

    def fetch_all(
        self,
        plan: list[SourcePlanItem],
        buybox: BuyBox,
        *,
        max_places: int = _DEFAULT_MAX_PLACES,
    ) -> list[CompanyRecord]:
        """Iterate the plan, derive buy-box params, run each connector, aggregate."""
        raw_pool: list[CompanyRecord] = []

        for item in plan:
            entry = self._entries.get(item.source_id)
            if entry is None or not entry.connector_ref or not entry.enabled:
                continue
            if entry.gate == "shortlist_only":
                continue  # LinkedIn and similar gated enrichment connectors
            if item.source_id in _ENRICHMENT_SOURCES:
                continue  # enrichment/screening/resolver sources run post-discovery

            connector = self._get_connector(entry.connector_ref)
            if connector is None:
                continue

            tiles = params_for_connector(
                item.source_id, buybox, max_places=max_places
            )
            for params in tiles:
                try:
                    raws = connector.fetch(params)
                    raw_pool.extend(connector.normalize(r) for r in raws)
                except Exception as exc:  # noqa: BLE001
                    warnings.warn(
                        f"SourcingOrchestrator: {item.source_id} fetch failed: {exc}",
                        stacklevel=2,
                    )

        return raw_pool

    def _get_connector(self, connector_ref: str) -> Any | None:
        try:
            return self._conn_registry.get_or_create(connector_ref)
        except Exception as exc:
            warnings.warn(
                f"SourcingOrchestrator: could not load connector {connector_ref!r}: {exc}",
                stacklevel=2,
            )
            return None
