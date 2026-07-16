"""NATAConnector — NATA accreditation register as a discovery + enrichment source.

Runs the Apify ``apify/playwright-scraper`` actor (the results page is a JS-rendered
SPA; the WordPress REST API is 403-blocked) tiled per state x include-keyword. The
pageFunction yields one row per accredited SITE; ``_group_by_parent`` rolls sites up
to one record per parent org, and ``normalize`` keeps only private_commercial parents
(via the ownership classifier). Every failure degrades to zero records — never raises.
"""
from __future__ import annotations

import math
import re
import warnings
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "nata_accreditation"
_MAX_PAGES = 25  # hard cap per (state x keyword) tile
_RESULTS_PER_PAGE = 20  # NATA default page size; verify empirically on first live run

# Mirror the entity resolver's name normalization (entity_resolution.py:28,72) so
# NATA parents aggregate the same way the resolver later matches them.
_SUFFIX = re.compile(r"\b(pty\s*ltd|pty\s*limited|limited|ltd|& co|and co|inc|corp)\b", re.I)


def normalize_org_name(name: str) -> str:
    # Strip periods first so "Pty. Ltd." and "Pty Ltd" collapse to the same
    # suffix form before the regex strips it (entity_resolution.py does the same).
    stripped = _SUFFIX.sub("", (name or "").replace(".", ""))
    return re.sub(r"\s+", " ", stripped).strip().lower()


# The browser-side extractor. Kept as a string — it runs inside the Apify actor,
# not in this process. Selectors match on class-contains + structure, not exact
# Tailwind class names, so minor CSS churn doesn't break it.
_PAGE_FUNCTION = r"""
async function pageFunction(context) {
  const { page, request } = context;
  await page.waitForSelector('div:has-text("results")', { timeout: 20000 }).catch(() => {});
  return await page.evaluate(() => {
    const rows = [];
    const cards = Array.from(document.querySelectorAll('div')).filter(
      d => d.querySelector('a[href*="/site/"]'));
    let total = null;
    const rc = document.body.innerText.match(/([\d,]+)\s+results/i);
    if (rc) total = parseInt(rc[1].replace(/,/g, ''), 10);
    for (const card of cards) {
      const text = card.innerText || '';
      const acc = text.match(/Accreditation No\.?\s*(\d+)/i);
      const site = text.match(/Site No\.?\s*(\d+)/i);
      const parentP = card.querySelector('p');
      const link = card.querySelector('a[href*="/site/"]');
      rows.push({
        parent_org: parentP ? parentP.innerText.trim() : '',
        site_name: link ? link.innerText.trim() : '',
        accreditation_number: acc ? acc[1] : null,
        site_number: site ? site[1] : null,
        address: text.split('\n').pop().trim(),
      });
    }
    return [{ _sentinel: true, _total_results: total }, ...rows];
  });
}
"""


def _split_total(rows: list[dict], search: str) -> tuple[int | None, list[dict]]:
    """Split the leading sentinel row(s) from real card rows.

    Returns ``(total, card_rows)`` — ``total`` is the sentinel's ``_total_results``
    (``None`` if no sentinel was found), and ``card_rows`` are the remaining rows
    with ``service`` defaulted from ``search``.
    """
    total = None
    cards = []
    for r in rows:
        if r.get("_sentinel"):
            total = r.get("_total_results")
        else:
            r.setdefault("service", search)
            cards.append(r)
    return total, cards


class NATAConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "apify/playwright-scraper"
    cache_ttl_seconds: int = 7 * 24 * 3600  # weekly freshness

    def __init__(self, *, cache: Any = None, client: Any = None, classifier: Any = None) -> None:
        super().__init__(cache=cache, client=client)
        self._classifier = classifier

    def _get_classifier(self):
        if self._classifier is None:
            from ..classifiers.ownership_classifier import OwnershipClassifier

            self._classifier = OwnershipClassifier()
        return self._classifier

    def _build_url(self, state: str, search: str = "", filter_by: str = "service",
                   status: str = "active", page: int = 1) -> str:
        params = urlencode({"post_type": "site", "s": search, "filter": filter_by,
                            "state": state, "status": status})
        return f"https://nata.com.au/page/{page}/?{params}"

    def build_input(self, params: dict) -> dict:
        state = params.get("state", "")
        search = params.get("search", "")
        pages = min(int(params.get("pages", 1)), _MAX_PAGES)
        start = max(1, int(params.get("start_page", 1)))
        filter_by = params.get("filter_by", "service")
        status = params.get("status", "active")
        urls = [{"url": self._build_url(state, search, filter_by, status, p)}
                for p in range(start, pages + 1)]
        return {
            "startUrls": urls,
            "pageFunction": _PAGE_FUNCTION,
            "waitUntil": ["networkidle2"],
            "proxyConfiguration": {"useApifyProxy": True},
            # carry the search term through so normalize can seed service types
            "_search": search,
        }

    def _fetch_sites(self, params: dict) -> list[dict]:
        """Run tile page 1, size the sweep from the sentinel's total, fetch the rest."""
        first = self._run_actor(self.build_input({**params, "pages": 1}))
        total, card_rows = _split_total(first, params.get("search", ""))
        if total and not card_rows:
            warnings.warn(
                "NATAConnector: non-zero results but 0 sites extracted — NATA page "
                "structure may have changed",
                stacklevel=2,
            )
            return []
        if not card_rows:
            return []
        if not total:
            return card_rows
        pages = min(math.ceil(total / _RESULTS_PER_PAGE), _MAX_PAGES)
        if pages <= 1:
            return card_rows
        _, rest_rows = _split_total(
            self._run_actor(self.build_input({**params, "start_page": 2, "pages": pages})),
            params.get("search", ""),
        )
        # dedupe by (accreditation_number, site_number)
        seen: set[tuple] = set()
        out: list[dict] = []
        for r in [*card_rows, *rest_rows]:
            key = (r.get("accreditation_number"), r.get("site_number"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    def _group_by_parent(self, raws: list[dict]) -> list[dict]:
        groups: OrderedDict[str, dict] = OrderedDict()
        for r in raws:
            key = normalize_org_name(r.get("parent_org", ""))
            if not key:
                continue
            g = groups.get(key)
            if g is None:
                g = {"parent_org": r.get("parent_org", ""), "normalized": key,
                     "sites": [], "accreditation_numbers": [], "states": [],
                     "service_types": [], "state_counts": {}}
                groups[key] = g
            g["sites"].append({"site_name": r.get("site_name"),
                               "site_number": r.get("site_number"),
                               "address": r.get("address")})
            acc = r.get("accreditation_number")
            if acc and acc not in g["accreditation_numbers"]:
                g["accreditation_numbers"].append(acc)
            st = _state_of(r)
            if st:
                g["states"].append(st) if st not in g["states"] else None
                g["state_counts"][st] = g["state_counts"].get(st, 0) + 1
            svc = r.get("service")
            if svc and svc not in g["service_types"]:
                g["service_types"].append(svc)
        for g in groups.values():
            g["site_count"] = len(g["sites"])
        return list(groups.values())

    def fetch(self, params: dict) -> list[CompanyRecord]:
        return self._build_records(self._fetch_sites(params))

    def normalize(self, record: CompanyRecord) -> CompanyRecord:
        """Identity — records are already built (and classifier-gated) by ``fetch``.

        The orchestrator calls ``normalize(r) for r in raws``; since ``fetch``
        already returns finished ``CompanyRecord``s here, this just passes them
        through unchanged.
        """
        return record

    def _build_records(self, raws: list[dict]) -> list[CompanyRecord]:
        """Aggregate raw site rows into parents, classify, and keep only private."""
        from ..classifiers.ownership_classifier import PRIVATE

        parents = self._group_by_parent(raws)
        if not parents:
            return []
        try:
            results = self._get_classifier().classify([p["parent_org"] for p in parents])
        except Exception as exc:  # noqa: BLE001 - degrade to no NATA rows, never raise
            warnings.warn(f"NATAConnector: classifier failed, dropping NATA rows: {exc}", stacklevel=2)
            return []

        records = []
        for parent, cls in zip(parents, results, strict=False):
            if cls.category != PRIVATE:
                continue
            records.append(self._to_record(parent, cls))
        return records

    def _to_record(self, parent: dict, cls: Any) -> CompanyRecord:
        from ..models.company import CompanyRecord, Location, MoatSignals, Provenance, Sector

        states = parent["states"]
        counts = parent["state_counts"]
        primary = max(states, key=lambda s: counts.get(s, 0)) if states else None
        acc_nums = parent["accreditation_numbers"]
        locator = f"Accreditation #{acc_nums[0]}" if acc_nums else "NATA"
        if len(acc_nums) > 1:
            locator += f" + {len(acc_nums) - 1} others"
        flags = []
        if 0.5 <= cls.confidence < 0.8:
            flags.append("nata_classification_uncertain")
        return CompanyRecord(
            entity_id=f"nata:{parent['normalized']}",
            abn=None,
            legal_name=parent["parent_org"],
            country="Australia",
            location=Location(state=primary),
            sector=Sector(category_text=list(parent["service_types"])),
            moat_signals=MoatSignals(
                regulatory_accreditation=True,
                nata_accreditation=True,
                nata_site_count=parent["site_count"],
                nata_service_types=list(parent["service_types"]),
                nata_accreditation_numbers=list(acc_nums),
                nata_states=list(states),
                nata_multistate=len(states) > 1,
            ),
            provenance=[Provenance(field="nata_accreditation", source="nata",
                                   locator=locator, confidence=0.95)],
            flags=flags,
            resolution_confidence=0.0,
        )


_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b")


def _state_of(r: dict) -> str | None:
    st = (r.get("state") or "").strip().upper()
    if st:
        return st
    m = _STATE_RE.search(r.get("address") or "")
    return m.group(1) if m else None
