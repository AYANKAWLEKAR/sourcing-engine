"""Award-register discovery demo — the first AgentConnector end to end.

Sweeps Telstra Best of Business finalist pages (rag-web-browser + qwen), resolves the
finalist companies to the ABN spine, and shows them entering the pool with
`award_finalist=True` — then ranks a small subset so the award signal shows up in the
judge + standout chips. No ABNs in; award-anchored CompanyRecords out.

Run: python scripts/awards_demo.py
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

from sourcing.connectors.awards import TelstraAwardsConnector  # noqa: E402
from sourcing.connectors.cache import InMemoryTTLCache  # noqa: E402
from sourcing.enrichment.entity_resolution import EntityResolver  # noqa: E402
from sourcing.rank.buybox import BuyBox  # noqa: E402
from sourcing.rank.rank import rank_pool  # noqa: E402

CATEGORIES = ["embracing-innovation"]   # 1 page keeps Apify + qwen time reasonable
RANK_CAP = 4                            # cap the qwen judge calls

BUYBOX = BuyBox(
    thesis="Innovative founder-owned Australian SMBs (award-quality)",
    sector_keywords=["software", "technology", "services", "engineering", "consulting"],
    states=["QLD", "NSW", "VIC", "SA", "WA", "TAS", "NT", "ACT"],
    target_models=["B2B"],
)


def main() -> None:
    print("=== Award-register discovery demo: Telstra Best of Business 2025 ===")
    connector = TelstraAwardsConnector(cache=InMemoryTTLCache())
    resolver = EntityResolver()

    print(f"1. Sweep finalist pages {CATEGORIES} (rag-web-browser → qwen extract)…")
    raw = connector.fetch({"year": 2025, "categories": CATEGORIES})
    records = [connector.normalize(r) for r in raw]
    print(f"   {len(records)} finalists extracted (all carry award_finalist=True, no ABN)\n")

    print("2. Resolve finalist names → ABN spine (name + state, no postcode)…")
    for rec in records:
        resolver.enrich(rec)
    resolved = [r for r in records if r.abn]
    rate = len(resolved) / len(records) if records else 0
    print(f"   resolved {len(resolved)}/{len(records)} ({rate:.0%}) to an ABN\n")

    print("   Award finalists anchored to the ABN spine:")
    print(f"   {'finalist':<34} {'state':<5} {'abn':<13} {'category (LLM)':<22} award")
    print("   " + "-" * 88)
    for r in resolved[:12]:
        cat = (r.sector.category_text or [""])[0][:20]
        print(f"   {(r.legal_name or '')[:33]:<34} {(r.location.state or '?'):<5} {r.abn:<13} "
              f"{cat:<22} {r.moat_signals.award_finalist}")

    # 3. Rank a small subset so the award signal shows up in the judge + card.
    subset = resolved[:RANK_CAP] or records[:RANK_CAP]
    if subset:
        print(f"\n3. Rank {len(subset)} (qwen judge weighs the award-finalist signal)…")
        ranked = rank_pool(subset, BUYBOX, top_k=RANK_CAP, judge_k=RANK_CAP)
        resolver.asic.close()
        print(f"\n   {'#':>2}  {'company':<32} {'S_final':>7} {'judge':>5}  standout signals")
        print("   " + "-" * 78)
        for i, rc in enumerate(ranked, 1):
            sig = "; ".join(rc.standout_signals[:3]) or "(none)"
            print(f"   {i:>2}  {(rc.record.legal_name or '')[:31]:<32} {rc.s_final:>7.3f} "
                  f"{rc.judge_fit or 0:>5.2f}  {sig[:44]}")
        if ranked:
            top = ranked[0]
            print(f"\n   top: {top.record.legal_name}  award_signals={[(a.program, a.tier, a.category) for a in top.record.award_signals]}")
    else:
        resolver.asic.close()

    print(f"\nRESULT: {len(resolved)}/{len(records)} Telstra finalists anchored to the ABN spine, "
          f"each carrying a tier-1 award-finalist signal into ranking.")


if __name__ == "__main__":
    main()
