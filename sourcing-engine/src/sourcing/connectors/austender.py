"""AusTenderConnector — government contract signal (addendum §3, reconciled to the real API).

AusTender's OCDS API is public, free, and no-auth. **Reality check vs the addendum:**
the OCDS API has NO per-supplier-ABN endpoint — only date-range queries
(``findByDates/{dateType}/{start}/{end}``). Supplier ABN lives in
``parties[].additionalIdentifiers[]`` (scheme ``AU-ABN``); the buyer role is
``procuringEntity``. So ``fetch({abn})`` scans a bounded recent window and filters
by supplier ABN client-side. The window response is cached, so looking up many
ABNs against the same window costs one HTTP call.

Comprehensive per-ABN history (beyond the window) needs the bulk OCDS dataset
ingested as a BulkConnector — a noted follow-up. This connector sets the moat
signal honestly for suppliers found in the scanned window, and records an explicit
"checked, none found" flag otherwise.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .base_api import APIConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "austender"
_ABN_SCHEME = "AU-ABN"


class AusTenderConnector(APIConnector):
    """AusTender OCDS API connector. Public Australian government contract data."""

    source_id: str = SOURCE_ID
    base_url: str = "https://api.tenders.gov.au/ocds"
    rate_limit_rps: float = 2.0          # public service — be polite
    cache_ttl_seconds: int = 86400 * 7   # weekly; contracts don't churn fast

    # Default scan window for a supplier lookup (recent contracts).
    default_window_days: int = 180
    max_pages: int = 3                   # bound the scan (≈ pages × API page size)

    # ---------- fetch ----------------------------------------------------------

    def fetch(self, params: dict) -> list[dict]:
        """``params``: ``{"abn": str, "start_date"?, "end_date"?}``.

        Returns OCDS release dicts whose supplier ABN matches (``[]`` = none in
        the scanned window). The window scan is cached, so repeated ABN lookups
        against the same window reuse one HTTP response.
        """
        abn = (params.get("abn") or "").replace(" ", "")
        if len(abn) != 11 or not abn.isdigit():
            return []  # bad ABN → no HTTP call

        end = params.get("end_date") or _now_z()
        start = params.get("start_date") or _days_ago_z(self.default_window_days)

        releases = self._scan_window(start, end)
        return [r for r in releases if _supplier_abn(r) == abn]

    def _scan_window(self, start: str, end: str) -> list[dict]:
        """Fetch all contract-published releases in [start, end], bounded by max_pages."""
        url = f"{self.base_url}/findByDates/contractPublished/{start}/{end}"
        out: list[dict] = []
        for _ in range(self.max_pages):
            data = self._get(url)
            if not isinstance(data, dict):
                break
            out.extend(data.get("releases") or [])
            nxt = (data.get("links") or {}).get("next")
            if not nxt:
                break
            url = nxt
        return out

    # ---------- normalize ------------------------------------------------------

    def normalize(self, raw: dict) -> CompanyRecord:
        """Map one OCDS release → a CompanyRecord fragment with the moat signal."""
        from ..models.company import CompanyRecord, Provenance

        rec = CompanyRecord()
        rec.abn = _supplier_abn(raw)
        rec.moat_signals.gov_contracts = True
        rec.moat_signals.gov_contract_value_aud = _release_value(raw)
        rec.provenance.append(
            Provenance(field="moat_signals.gov_contracts", source=SOURCE_ID,
                       locator=raw.get("ocid", ""), fetched_at=_now_z(), confidence=0.95)
        )
        return rec

    # ---------- aggregator (used by the enrichment node) ----------------------

    def enrich_record(self, record: CompanyRecord, window: dict | None = None) -> CompanyRecord:
        """Look up the record's ABN, aggregate matching releases, merge onto it.

        ``window`` optionally narrows the scan: ``{"start_date":..., "end_date":...}``
        (defaults to the recent window). Records the explicit "checked and clear"
        flag when none are found — absence is a fact, not a gap.
        """
        from ..models.company import Provenance

        if not record.abn:
            return record

        releases = self.fetch({"abn": record.abn, **(window or {})})
        if not releases:
            record.flags.append("austender_checked_no_contracts")
            record.moat_signals.gov_contracts = False
            return record

        total = sum(_release_value(r) for r in releases)
        agencies = sorted({a for r in releases for a in _agencies(r)})

        record.moat_signals.gov_contracts = True
        record.moat_signals.gov_contract_value_aud = total
        record.moat_signals.gov_contract_agencies = agencies
        record.moat_signals.gov_contract_count = len(releases)
        record.provenance.append(
            Provenance(field="moat_signals.gov_contracts", source=SOURCE_ID,
                       locator=f"supplierABN={record.abn}; {len(releases)} releases",
                       fetched_at=_now_z(), confidence=0.95)
        )
        return record


# ---------------------------------------------------------------------------
# OCDS release helpers
# ---------------------------------------------------------------------------

def _supplier_abn(release: dict) -> str | None:
    for p in release.get("parties") or []:
        if "supplier" in (p.get("roles") or []):
            for ident in p.get("additionalIdentifiers") or []:
                if ident.get("scheme") == _ABN_SCHEME and ident.get("id"):
                    return str(ident["id"])
            # Fallback to the primary identifier if additionalIdentifiers absent.
            ident = p.get("identifier") or {}
            if ident.get("id"):
                return str(ident["id"])
    return None


def _agencies(release: dict) -> list[str]:
    out = []
    for p in release.get("parties") or []:
        roles = p.get("roles") or []
        if ("procuringEntity" in roles or "buyer" in roles) and p.get("name"):
            out.append(p["name"])
    return out


def _to_num(value) -> float:
    """OCDS amounts sometimes arrive as strings; coerce safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _release_value(release: dict) -> int:
    total = 0.0
    for c in release.get("contracts") or []:
        total += _to_num((c.get("value") or {}).get("amount"))
    if not total:
        for a in release.get("awards") or []:
            total += _to_num((a.get("value") or {}).get("amount"))
    return int(total)


def _now_z() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago_z(days: int) -> str:
    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
