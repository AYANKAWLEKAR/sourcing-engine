"""End-to-end enrichment + ranking demo (next-phase plan Parts A+B).

One buy-box slice, no ABNs in → a ranked shortlist out:

    Maps discovery → EntityResolver → AusTender + Website→qwen signal extract
                   → screen → statistical score → qwen judge → ranked top-N

All LLM work runs on a LOCAL Ollama/qwen model (no cloud API).
Run: python scripts/rank_demo.py
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

from sourcing.config import get_settings  # noqa: E402
from sourcing.connectors.cache import InMemoryTTLCache  # noqa: E402
from sourcing.connectors.google_maps import GoogleMapsConnector  # noqa: E402
from sourcing.connectors.website import WebsiteFetchConnector  # noqa: E402
from sourcing.enrichment.enrichment_node import EnrichmentNode  # noqa: E402
from sourcing.enrichment.entity_resolution import EntityResolver  # noqa: E402
from sourcing.rank.buybox import BuyBox  # noqa: E402
from sourcing.rank.rank import rank_pool  # noqa: E402

# --- Buy box: HVAC installers in Brisbane QLD ---------------------------------
BUYBOX = BuyBox(
    thesis="Founder-owned HVAC and air-conditioning installers/servicers in Brisbane QLD",
    sector_keywords=["hvac", "air conditioning", "refrigeration", "heating", "ventilation", "mechanical services"],
    sector_exclude_keywords=["retail showroom", "real estate", "hotel"],
    anzsic=["3223"],
    states=["QLD"],
    target_models=["B2B"],
    min_years=3,
)

DISCOVERY = {
    "search_terms": ["HVAC installer", "air conditioning services"],
    "location": "Brisbane QLD Australia",
    "max_places": 15,   # small slice — keeps Apify + local-LLM time reasonable
}
# CPU-only Docker qwen is slow (~1 min/call), so cap the LLM pool for the demo.
ENRICH_CAP = 5


def main() -> None:
    s = get_settings()
    print("=== Enrichment + Ranking demo: HVAC installers, Brisbane QLD ===")
    print(f"LLM: Ollama @ {s.ollama_host}  enrich={s.enrich_model}  judge={s.judge_model}\n")

    maps = GoogleMapsConnector(cache=InMemoryTTLCache())
    resolver = EntityResolver()

    print("1. Discover (Google Maps) + resolve to ABN spine…")
    raw = maps.fetch(DISCOVERY)
    records = [maps.normalize(r) for r in raw]
    for rec in records:
        resolver.enrich(rec)
    resolved = [r for r in records if r.abn]
    print(f"   {len(records)} discovered → {len(resolved)} resolved to an ABN")

    # Cap the pool we enrich (website fetch + LLM) to keep the demo quick/cheap.
    pool = [r for r in resolved if r.contacts_min.get("website")][:ENRICH_CAP]
    print(f"   enriching {len(pool)} resolved records that have a website\n")

    print("2. Enrich (AusTender gov-contracts + website→qwen signals)…")
    node = EnrichmentNode(website=WebsiteFetchConnector(cache=InMemoryTTLCache()))
    node.enrich_pool(pool, BUYBOX)
    n_model = sum(1 for r in pool if r.business_model and r.business_model != "UNKNOWN")
    n_gov = sum(1 for r in pool if r.moat_signals.gov_contracts)
    print(f"   {n_model}/{len(pool)} got a business_model; {n_gov} have gov contracts\n")

    print("3. Screen → score → qwen judge → rank…")
    ranked = rank_pool(pool, BUYBOX, top_k=10, judge_k=ENRICH_CAP)
    resolver.asic.close()

    print(f"\n=== RANKED SHORTLIST (top {len(ranked)}) ===")
    print(f"{'#':>2}  {'company':<32} {'state':<5} {'S_final':>7} {'S_stat':>6} {'judge':>5}  signals / rationale")
    print("-" * 110)
    for i, rc in enumerate(ranked, 1):
        r = rc.record
        sig = "; ".join(rc.standout_signals[:2]) or rc.judge_rationale[:48]
        print(f"{i:>2}  {(r.legal_name or '')[:31]:<32} {(r.location.state or ''):<5} "
              f"{rc.s_final:>7.3f} {rc.s_stat:>6.1f} {rc.judge_fit or 0:>5.2f}  {sig[:50]}")

    if ranked:
        top = ranked[0]
        print("\n--- top pick detail ---")
        print(f"  {top.record.legal_name}  (ABN {top.record.abn}, ACN {top.record.acn or '—'})")
        print(f"  model={top.record.business_model}  years={top.record.age.years_operating}  "
              f"anzsic={top.record.sector.anzsic}")
        print(f"  judge: {top.judge_rationale}")
        print(f"  standout: {top.standout_signals}")
        print(f"  deferred ({len(top.deferred_assessment)}): {top.deferred_assessment}")
        print(f"  provenance fields: {sorted({p.field for p in top.record.provenance})}")


if __name__ == "__main__":
    main()
