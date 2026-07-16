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
from urllib.parse import urlencode

from .base_scrape import ScrapeConnector

SOURCE_ID = "nata_accreditation"
_MAX_PAGES = 25  # hard cap per (state x keyword) tile

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
        _total_results: total,
      });
    }
    return rows;
  });
}
"""


class NATAConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "apify/playwright-scraper"
    cache_ttl_seconds: int = 7 * 24 * 3600  # weekly freshness

    def _build_url(self, state: str, search: str = "", filter_by: str = "service",
                   status: str = "active", page: int = 1) -> str:
        params = urlencode({"post_type": "site", "s": search, "filter": filter_by,
                            "state": state, "status": status})
        return f"https://nata.com.au/page/{page}/?{params}"

    def build_input(self, params: dict) -> dict:
        state = params.get("state", "")
        search = params.get("search", "")
        pages = min(int(params.get("pages", 1)), _MAX_PAGES)
        filter_by = params.get("filter_by", "service")
        status = params.get("status", "active")
        urls = [{"url": self._build_url(state, search, filter_by, status, p)}
                for p in range(1, pages + 1)]
        return {
            "startUrls": urls,
            "pageFunction": _PAGE_FUNCTION,
            "waitUntil": ["networkidle2"],
            "proxyConfiguration": {"useApifyProxy": True},
            # carry the search term through so normalize can seed service types
            "_search": search,
        }

    def _fetch_sites(self, params: dict) -> list[dict]:
        """Run tile page 1, size the sweep from _total_results, fetch the rest."""
        first = self._run_actor(self.build_input({**params, "pages": 1}))
        if not first:
            return []
        total = first[0].get("_total_results")
        for r in first:
            r.setdefault("service", params.get("search", ""))
        if not total:
            return first
        pages = min(math.ceil(total / 20), _MAX_PAGES)
        if pages <= 1:
            return first
        rest = self._run_actor(self.build_input({**params, "pages": pages}))
        for r in rest:
            r.setdefault("service", params.get("search", ""))
        # dedupe by (accreditation_number, site_number)
        seen: set[tuple] = set()
        out: list[dict] = []
        for r in [*first, *rest]:
            key = (r.get("accreditation_number"), r.get("site_number"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        if total and not out:
            warnings.warn(
                "NATAConnector: non-zero results but 0 extracted — site structure may have changed",
                stacklevel=2,
            )
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


_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b")


def _state_of(r: dict) -> str | None:
    st = (r.get("state") or "").strip().upper()
    if st:
        return st
    m = _STATE_RE.search(r.get("address") or "")
    return m.group(1) if m else None
