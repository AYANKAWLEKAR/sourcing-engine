"""Deduplication helpers for the candidate pool (audit Fix 9).

The same business appears in both Google Maps and Yellow Pages results.
Without deduplication, the EntityResolver bills two ABN Lookup API calls for the
same name, and EnrichmentNode runs AusTender + website-fetch twice on the same ABN.

``deduplicate_by_abn`` removes duplicate resolved records (keeping the one with
the richest provenance) and passes unresolved records through unchanged.
``deduplicate_pre_resolution`` removes records with the same (name, postcode) pair
before the resolver runs, saving API calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.company import CompanyRecord


def deduplicate_by_abn(records: list[CompanyRecord]) -> list[CompanyRecord]:
    """Deduplicate resolved records by ABN.

    For two records with the same ABN, keeps the one with more provenance entries
    (richer data).  Records without an ABN are passed through unchanged.
    """
    seen: dict[str, CompanyRecord] = {}
    unresolved: list[CompanyRecord] = []

    for rec in records:
        if not rec.abn:
            unresolved.append(rec)
            continue
        existing = seen.get(rec.abn)
        if existing is None or len(rec.provenance) > len(existing.provenance):
            seen[rec.abn] = rec

    return list(seen.values()) + unresolved


def deduplicate_pre_resolution(records: list[CompanyRecord]) -> list[CompanyRecord]:
    """Remove duplicate scrape results before resolution to save API calls.

    Groups records by normalised ``(legal_name, postcode)``; when two records share
    both, keeps the one with more contacts (website > phone > empty).  Records that
    lack both name and postcode are always kept.
    """
    seen: dict[tuple[str, str], CompanyRecord] = {}
    no_key: list[CompanyRecord] = []

    for rec in records:
        name = (rec.legal_name or "").strip().lower()
        postcode = (rec.location.postcode or "").strip()
        if not name and not postcode:
            no_key.append(rec)
            continue
        key = (name, postcode)
        existing = seen.get(key)
        if existing is None or _contact_richness(rec) > _contact_richness(existing):
            seen[key] = rec

    return list(seen.values()) + no_key


def _contact_richness(rec: CompanyRecord) -> int:
    c = rec.contacts_min
    return bool(c.get("website")) * 2 + bool(c.get("phone"))
