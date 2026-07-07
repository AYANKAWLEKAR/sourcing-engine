"""CompanyRecordCache — persistent ABN-keyed cache of enrichment results.

Enrichment's expensive external calls (the Apify website fetch, the AusTender
OCDS window scan, the IPGOD/ASX lookups) all key off a resolved ABN. Once a
company has been enriched, its external-source signals don't change run-to-run,
so we persist them keyed by ABN and reuse them on the next run — skipping the
Apify credits and the slow scan.

Only the *buy-box-independent* enrichment is cached and reapplied: the website
text and the IPGOD/AusTender/ASX moat/ownership signals. The buy-box-specific
signal extraction (keyword hits, business model, ANZSIC guess) is always
recomputed by ``SignalExtractor`` against the cached text — so a cache hit still
scores correctly for the current buy-box, it just avoids the network.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

# Provenance sources whose fields are reapplied verbatim on a cache hit.
_EXTERNAL_SOURCES = frozenset({"ipgod", "austender", "asx_listed_list", "website_fetch"})
# Flags recorded by those external steps (so "checked, none found" survives too).
_EXTERNAL_FLAGS = frozenset({
    "austender_checked_no_contracts", "ipgod_checked_no_ip", "asx_name_match_only",
})


class CompanyRecordCache:
    """SQLite-backed store of enriched CompanyRecords keyed by ABN, with TTL."""

    def __init__(self, path: str, *, ttl_seconds: int, clock: Any = time.time) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS record_cache "
            "(abn TEXT PRIMARY KEY, record TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        self._conn.commit()

    @classmethod
    def from_settings(cls) -> CompanyRecordCache | None:
        """Return a cache when the sqlite backend is configured, else None."""
        from ..config import get_settings

        s = get_settings()
        if s.cache_backend != "sqlite":
            return None
        return cls(s.cache_path, ttl_seconds=s.record_cache_ttl_seconds)

    def get(self, abn: str) -> CompanyRecord | None:
        from ..models.company import CompanyRecord

        with self._lock:
            row = self._conn.execute(
                "SELECT record, expires_at FROM record_cache WHERE abn = ?", (abn,)
            ).fetchone()
            if row is None:
                return None
            record_json, expires_at = row
            if self._clock() >= expires_at:
                self._conn.execute("DELETE FROM record_cache WHERE abn = ?", (abn,))
                self._conn.commit()
                return None
        return CompanyRecord(**json.loads(record_json))

    def put(self, record: CompanyRecord) -> None:
        if not record.abn:
            return
        blob = json.dumps(record.model_dump(), default=str)
        expires_at = self._clock() + self._ttl
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO record_cache (abn, record, expires_at) VALUES (?, ?, ?)",
                (record.abn, blob, expires_at),
            )
            self._conn.commit()


def apply_cached_enrichment(target: CompanyRecord, cached: CompanyRecord) -> None:
    """Overlay the external-source enrichment from ``cached`` onto ``target``.

    Copies the website text and the IPGOD / AusTender / ASX signals (same ABN →
    same facts), plus their provenance and "checked" flags. Leaves identity and
    discovery fields on ``target`` untouched; the buy-box-specific keyword
    extraction is recomputed afterward by the signal extractor.
    """
    if cached.website_text_raw and not target.website_text_raw:
        target.website_text_raw = cached.website_text_raw

    ms, cms = target.moat_signals, cached.moat_signals
    # IPGOD
    ms.ip, ms.ip_count, ms.ip_types = cms.ip, cms.ip_count, list(cms.ip_types)
    # AusTender
    ms.gov_contracts = cms.gov_contracts
    ms.gov_contract_value_aud = cms.gov_contract_value_aud
    ms.gov_contract_count = cms.gov_contract_count
    ms.gov_contract_agencies = list(cms.gov_contract_agencies)
    # ASX
    if cached.ownership.listed_entity is not None:
        target.ownership.listed_entity = cached.ownership.listed_entity

    # Carry over the external-source provenance and "checked" flags.
    target.provenance.extend(p for p in cached.provenance if p.source in _EXTERNAL_SOURCES)
    for flag in cached.flags:
        if flag in _EXTERNAL_FLAGS and flag not in target.flags:
            target.flags.append(flag)
