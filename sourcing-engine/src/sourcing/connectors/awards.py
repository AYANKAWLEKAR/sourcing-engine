"""Award-register connectors — the first concrete AgentConnector(s) (scrape plan §3.13).

An award register is a **discovery source**: it sweeps public finalist pages, extracts the
finalist businesses, and yields a curated pool of pre-vetted quality SMBs. Each becomes a
`CompanyRecord` with `moat_signals.award_finalist=True` (which the ranker's judge already
weighs) and an `AwardSignal`. No ABN — the EntityResolver anchors them downstream, exactly
like a Google Maps record.

Extraction strategy:
  * **name + state** are pulled *structurally* from the page (``#### {name}`` / ``{state}
    Finalist`` blocks) — verbatim page facts, high confidence.
  * **category** is determined by **one LLM call** classifying every finalist's business
    sector (per direction — LLM-determined, not URL-slug-mapped), returned as a single JSON
    object whose ``categories`` list aligns by index to the finalists.
"""
from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any

from .base_agent import AgentConnector

if TYPE_CHECKING:
    from ..connectors.protocol import RawRecord
    from ..llm import LLMClient
    from ..models.company import CompanyRecord

# Structural finalist blocks: "#### {name}\n\n{state} Finalist\n\n{desc}".
_FINALIST_RE = re.compile(
    r"####\s*(.+?)\n+\s*([A-Za-z ]+?)\s+Finalist\b\s*\n+(.*?)(?=\n####|\Z)", re.S
)

_CLASSIFY_SYSTEM = (
    "You classify Australian businesses by sector. Respond with ONLY a JSON object "
    '{"categories": ["<2-4 word category>", ...]} whose list has exactly one entry per '
    "numbered business, in the same order (e.g. 'electrical contractor')."
)


class AwardRegisterConnector(AgentConnector):
    """Generalizable base for award-register discovery connectors.

    Subclasses set ``source_id``, ``program``, ``program_key``, ``tier``,
    ``base_url_template`` (a ``{year}`` slot), ``category_slugs``, and ``default_year``.
    """

    source_id: str = ""
    program: str = ""
    program_key: str = ""            # short slug used in entity_id
    tier: int = 2
    base_url_template: str = ""      # e.g. ".../alumni/{year}-state-finalists"
    category_slugs: tuple[str, ...] = ()
    default_year: int = 2025
    max_finalists: int = 60          # bound per page

    def __init__(self, *, cache: Any = None, client: Any = None, llm_client: LLMClient | None = None):
        super().__init__(cache=cache, client=client)
        self._text_llm = llm_client  # an LLMClient (has .chat); lazy default

    @property
    def text_llm(self) -> LLMClient:
        if self._text_llm is None:
            from ..llm import get_llm_client

            self._text_llm = get_llm_client()
        return self._text_llm

    # ------------------------------------------------------------------
    # fetch — structural name/state + one LLM call for categories
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """``params``: ``{"year"?: int, "categories"?: [slug, ...]}``."""
        from ..connectors.protocol import RawRecord

        year = int(params.get("year", self.default_year))
        slugs = params.get("categories") or self.category_slugs
        base = self.base_url_template.format(year=year)

        out: list[RawRecord] = []
        for slug in slugs:
            url = f"{base}/{slug}"
            md = self._fetch_page_markdown(url)  # raw page markdown (AgentConnector)
            blocks = _FINALIST_RE.findall(md)[: self.max_finalists]
            # Fix 13: detect silent page-layout breakage.  Count H4 headers as a
            # proxy for expected finalists; warn when structural extraction yields
            # far fewer blocks than headers (page changed, regex no longer matches).
            h4_count = md.count("\n####")
            if h4_count > 1 and len(blocks) < h4_count * 0.5:
                warnings.warn(
                    f"award_page_extraction_degraded: {url} — "
                    f"{h4_count} H4 headers but only {len(blocks)} structural blocks extracted. "
                    "The finalist page layout may have changed; check the _FINALIST_RE pattern.",
                    stacklevel=2,
                )
            if not blocks:
                continue
            categories = self._classify_categories(blocks)
            for (name, state, _desc), category in zip(blocks, categories, strict=False):
                name = name.strip()
                if not name:
                    continue
                state = _norm_state(state)
                out.append(
                    RawRecord(
                        source_id=self.source_id,
                        org_name=name,
                        state=state,
                        raw={"name": name, "state": state, "category": category,
                             "program": self.program, "tier": self.tier, "year": year, "url": url},
                    )
                )
        return out

    def _classify_categories(self, blocks: list[tuple[str, str, str]]) -> list[str | None]:
        """One JSON LLM call → a business category per finalist, aligned by index."""
        from ..config import get_settings
        from ..llm import complete_json

        listing = "\n".join(
            f"{i + 1}. {name.strip()} — {desc.strip()[:80]}"
            for i, (name, _state, desc) in enumerate(blocks)
        )
        data = complete_json(self.text_llm, get_settings().enrich_model, _CLASSIFY_SYSTEM, listing)
        cats = data.get("categories") if isinstance(data, dict) else None
        if not isinstance(cats, list):
            return [None] * len(blocks)
        out: list[str | None] = []
        for i in range(len(blocks)):
            raw = cats[i] if i < len(cats) else None
            cat = str(raw).strip() if raw else ""
            out.append(cat or None)
        return out

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import (
            AwardSignal,
            CompanyRecord,
            Location,
            MoatSignals,
            Provenance,
            Sector,
        )

        info = raw.get("raw", {})
        name = raw.get("org_name") or info.get("name")
        state = raw.get("state") or info.get("state")
        category = info.get("category")
        year = info.get("year")
        url = info.get("url", "")

        signal = AwardSignal(
            program=self.program, tier=self.tier, year=year,
            category=category, state=state, level=info.get("level", "finalist"),
        )
        return CompanyRecord(
            entity_id=f"award:{self.program_key}:{_slug(name)}",
            abn=None,  # resolved downstream by the EntityResolver
            legal_name=name,
            country="Australia",
            location=Location(state=state),
            sector=Sector(category_text=[category] if category else []),
            moat_signals=MoatSignals(award_finalist=True),
            award_signals=[signal],
            provenance=[
                # Verbatim facts from the page listing — high confidence.
                Provenance(field="award_finalist", source=self.source_id, locator=url, confidence=0.9),
                # LLM-classified business category — lower confidence.
                Provenance(field="sector", source=self.source_id, locator=url, confidence=0.5),
            ],
        )


_TRADES_EXTRACT_SYSTEM = (
    "You extract award finalists and winners from a page about the Australian "
    "Trades Small Business Champion Awards. Respond with ONLY a JSON object "
    '{"businesses": [{"name": "<business name>", "state": '
    '"NSW|VIC|QLD|SA|WA|TAS|NT|ACT", "category": "<trade, e.g. plumbing, '
    'electrical, air conditioning, building, carpentry>", "level": '
    '"winner|finalist"}]}. Convert full state names to the abbreviation. Include '
    "only businesses explicitly listed as finalists or winners; skip sponsors, "
    "presenters, judges, and article authors. Return an empty list if none."
)


class TradesChampionConnector(AwardRegisterConnector):
    """Australian Trades Small Business Champion Awards — tier-1 trade register.

    A discovery source of pre-vetted trade SMBs (plumbing, electrical, HVAC,
    building) — the exact sectors Origo's buy-boxes target. Finalists live on the
    official register (``championawards.com.au/trades/...``, rendered as tables)
    and in trade-press coverage; both are fetched as markdown and parsed by one
    JSON LLM extraction per page (page layouts vary too much for a single regex,
    unlike Telstra's structured finalist blocks). The trade *is* the sector hint,
    so no separate classification call is needed.
    """

    source_id: str = "trades_champion"
    program: str = "Australian Trades Small Business Champion"
    program_key: str = "trades_champion"
    tier: int = 1
    default_year: int = 2025
    # Full finalist/winner article URLs (pages span several domains, not base+slug).
    #
    # Sourcing note (verified live): the official championawards.com.au register is
    # a JS SPA that rag-web-browser can't render, and the trade-press *tag* pages
    # return empty — but the annual trade-press finalist-announcement *articles* do
    # render, and one such article lists finalists across every trade category
    # (~60 businesses, name + state). These URLs change each year (annual awards);
    # refresh them yearly or pass current ones via ``params["urls"]``. Cached 30
    # days, so a run re-fetches at most once a month.
    finalist_urls: tuple[str, ...] = (
        "https://electricalconnection.com.au/australian-trades-small-business-champion-awards-reveals-2024-finalists/",
        "https://www.fmmedia.com.au/sectors/high-calibre-of-finalists-for-australian-trades-small-business-champion-awards/",
    )

    def fetch(self, params: dict) -> list[RawRecord]:
        """``params``: ``{"year"?: int, "urls"?: [url, ...]}``."""
        from ..connectors.protocol import RawRecord

        year = int(params.get("year", self.default_year))
        urls = params.get("urls") or self.finalist_urls

        seen: set[tuple[str, str | None]] = set()
        out: list[RawRecord] = []
        for url in urls:
            md = self._fetch_page_markdown(url)
            if not md:
                continue
            businesses = self._extract_businesses(md)
            if len(md) > 500 and not businesses:
                warnings.warn(
                    f"trades_champion_extraction_empty: {url} returned "
                    f"{len(md)} chars of markdown but no finalists were extracted "
                    "(page layout may have changed or the page was blocked).",
                    stacklevel=2,
                )
            for biz in businesses[: self.max_finalists]:
                name = str(biz.get("name") or "").strip()
                if not name:
                    continue
                state = _norm_state(biz.get("state"))
                key = (name.lower(), state)
                if key in seen:
                    continue
                seen.add(key)
                level = "winner" if str(biz.get("level", "")).lower().startswith("win") else "finalist"
                out.append(
                    RawRecord(
                        source_id=self.source_id,
                        org_name=name,
                        state=state,
                        raw={
                            "name": name, "state": state,
                            "category": str(biz.get("category") or "").strip() or None,
                            "level": level, "program": self.program,
                            "tier": self.tier, "year": year, "url": url,
                        },
                    )
                )
        return out

    def _extract_businesses(self, markdown: str) -> list[dict]:
        """One JSON LLM call → the finalists/winners listed on the page."""
        from ..config import get_settings
        from ..llm import complete_json

        data = complete_json(
            self.text_llm, get_settings().enrich_model,
            _TRADES_EXTRACT_SYSTEM, markdown[:8000],
        )
        biz = data.get("businesses") if isinstance(data, dict) else None
        return biz if isinstance(biz, list) else []


class TelstraAwardsConnector(AwardRegisterConnector):
    """Telstra Best of Business Awards — tier-1 national award register."""

    source_id: str = "telstra_awards"
    program: str = "Telstra Best of Business"
    program_key: str = "telstra"
    tier: int = 1
    base_url_template: str = (
        "https://www.telstra.com.au/small-business/best-of-business-awards"
        "/alumni/{year}-state-finalists"
    )
    category_slugs: tuple[str, ...] = (
        "accelerating-women",
        "embracing-innovation",
        "outstanding-growth",
        "promoting-sustainability",
        "indigenous-excellence",
        "championing-health",
        "building-communities",
    )
    default_year: int = 2025


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AU_STATES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}

_STATE_FULL = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
    "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS",
    "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}


def _norm_state(value: Any) -> str | None:
    s = str(value or "").strip().upper()
    s = _STATE_FULL.get(s, s)
    return s if s in _AU_STATES else None


def _slug(value: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s or "unknown"
