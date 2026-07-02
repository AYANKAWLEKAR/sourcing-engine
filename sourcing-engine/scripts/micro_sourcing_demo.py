"""Micro-sourcing demo (plan §8) — the off-market discovery loop, clean output.

No ABNs in (just a category + a place), spine-anchored CompanyRecords out.
Run: python scripts/micro_sourcing_demo.py
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from sourcing.connectors.cache import InMemoryTTLCache  # noqa: E402
from sourcing.connectors.google_maps import GoogleMapsConnector  # noqa: E402
from sourcing.enrichment.entity_resolution import EntityResolver  # noqa: E402

BUYBOX = {
    "search_terms": ["HVAC installer", "air conditioning services"],
    "location": "Brisbane QLD Australia",
    "max_places": 40,
}

print("=== Off-market micro-sourcing: HVAC installers, Brisbane QLD ===")
print(f"Buy-box slice (no ABNs in): {BUYBOX['search_terms']} @ {BUYBOX['location']}\n")

maps = GoogleMapsConnector(cache=InMemoryTTLCache())
resolver = EntityResolver()

print("1. Discovering via Google Maps (Apify)…")
raw = maps.fetch(BUYBOX)
records = [maps.normalize(r) for r in raw]
print(f"   discovered {len(records)} candidates (category + location, NO abn)\n")

print("2. Resolving each to the ABN spine (ABN Lookup name-match -> ASIC merge)…")
for rec in records:
    resolver.enrich(rec)
resolver.asic.close()

resolved = [r for r in records if r.abn]
uncertain = [r for r in resolved if "abn_match_uncertain" in r.flags]
rate = len(resolved) / len(records) if records else 0
print(f"   resolved {len(resolved)}/{len(records)} ({rate:.0%}); "
      f"{len(uncertain)} flagged abn_match_uncertain\n")

print("3. Sample anchored CompanyRecords (off-market name -> spine identity):")
print(f"   {'maps name':<34} {'abn':<13} {'acn':<10} {'asic_reg':<11} rc")
print("   " + "-" * 78)
for r in resolved[:12]:
    cats = (r.sector.category_text or [""])[0][:18]
    print(f"   {(r.legal_name or '')[:33]:<34} {r.abn:<13} {(r.acn or ''):<10} "
          f"{(r.age.asic_registered or '')[:10]:<11} {r.resolution_confidence or 0:.2f}  [{cats}]")

print(f"\nRESULT: {len(resolved)}/{len(records)} HVAC Brisbane companies anchored to the "
      f"ABN spine ({rate:.0%} resolution). Every field carries provenance.")
