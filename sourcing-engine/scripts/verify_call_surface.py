"""Verify the call surface the orchestrator/agent invokes (plan §7).

Exercises every function the Source Planner and orchestrator call, end to end, on
a tiny live slice. Each step prints PASS/FAIL with the shape it got back.

Run: python scripts/verify_call_surface.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env into the environment so connectors see APIFY_API_TOKEN / GUID.
ENV = Path(__file__).resolve().parents[1] / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from sourcing.connectors.abn.lookup import ABNLookupAPIConnector  # noqa: E402
from sourcing.connectors.asic_bulk import ASICBulkConnector  # noqa: E402
from sourcing.connectors.google_maps import GoogleMapsConnector  # noqa: E402
from sourcing.connectors.loader import load_connector  # noqa: E402
from sourcing.enrichment.entity_resolution import EntityResolver  # noqa: E402

_results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label:32} {detail}")
    _results.append(ok)
    return ok


print("=== Call-surface verification (live slice) ===\n")

# 1. registry → correct base class
c = load_connector("sourcing.connectors.asic_bulk.ASICBulkConnector")
check("load_connector", isinstance(c, ASICBulkConnector), type(c).__name__)

# 2. spine slice (deduped)
asic = ASICBulkConnector.from_settings()
slice_ = asic.fetch({"state": "QLD", "min_years": 5, "status": "REGD", "limit": 5})
check("asic.fetch slice", len(slice_) > 0, f"{len(slice_)} rows")

# 3. point lookup
abn0 = slice_[0].get("abn") if slice_ else None
check("asic.lookup_abn", bool(abn0) and asic.lookup_abn(abn0) is not None, str(abn0))

# 4. live ABN detail + name match
api = ABNLookupAPIConnector.from_settings()
check("abn detail", bool(api.fetch({"abn": abn0})) if abn0 else False)
check("abn name match", len(api.fetch({"name": "testing", "state": "QLD"})) > 0)

# 5. scrape → normalize (needs APIFY token)
if os.environ.get("APIFY_API_TOKEN"):
    maps = GoogleMapsConnector()
    raw = maps.fetch({"search_terms": ["HVAC installer"],
                      "location": "Brisbane QLD Australia", "max_places": 5})
    rec = maps.normalize(raw[0])
    check("maps fetch+normalize", bool(rec.sector.category_text) and rec.abn is None,
          f"{len(raw)} places, cats={len(rec.sector.category_text)}")

    # 6. resolve → spine merge
    resolved = EntityResolver().enrich(rec)
    check("resolver enrich",
          resolved.abn is not None or "unresolved_abn" in resolved.flags,
          f"abn={resolved.abn} rc={resolved.resolution_confidence or 0:.2f}")
else:
    print("SKIP  scrape+resolve                  (APIFY_API_TOKEN not set)")

asic.close()
print("\n" + ("Call surface verified — all PASS." if all(_results)
              else f"FAILURES: {_results.count(False)}/{len(_results)}"))
sys.exit(0 if all(_results) else 1)
