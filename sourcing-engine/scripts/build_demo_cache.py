"""Build the demo-prompt cache by capturing one real pipeline run.

Runs the full engine once for the canonical demo buy box (HVAC installers, Sydney,
$1–5M EBITDA), then dumps the produced source plan, coverage, and ranked shortlist
to ``data/demo_cache/hvac_sydney.json``. The UI's demo replay serves that file so
the loading trace animates in seconds instead of minutes.

Usage:
    python scripts/build_demo_cache.py            # default caps (slower, richer)
    python scripts/build_demo_cache.py --fast     # smaller caps to build quickly

Spends Apify + Anthropic (Haiku enrich/judge) credits — it is a real run.
"""
from __future__ import annotations

import argparse
import sys

from sourcing.agent.tools import RulesetEditor, summarize_ruleset
from sourcing.config import get_settings
from sourcing.models.run import RunStatus
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs import demo_cache
from sourcing.runs.pipeline import RunPipeline
from sourcing.runs.store import InMemoryRunStore

BUY_BOX = "founder owned HVAC companies; 1-5M ebitda in all of sydney area"
CACHE_KEY = "hvac_sydney"

# HVAC lives in ANZSIC 3234 (Air Conditioning & Heating Services) with adjacent
# building-services classes; keywords drive the Maps/text discovery.
HVAC_ANZSIC = ["3234", "3231", "3232"]
HVAC_KEYWORDS = [
    "hvac",
    "air conditioning",
    "heating",
    "ventilation",
    "refrigeration",
    "ducted air conditioning",
    "split system",
]


def _build_ruleset():
    """The confirmed ruleset the demo prompt resolves to — set deterministically."""
    rs = load_origo_ruleset()
    editor = RulesetEditor(rs)
    editor.update_ruleset("anzsic_code", {"values": HVAC_ANZSIC})
    editor.update_ruleset(
        "sector_keyword_match",
        {"include": HVAC_KEYWORDS, "exclude": ["retail", "hospitality"]},
    )
    editor.resolve_geography(regions=["sydney"])  # Sydney → NSW postcodes
    editor.update_ruleset("ebitda_aud", {"min": 1_000_000, "max": 5_000_000})
    editor.finalize_ruleset()
    return editor.ruleset


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="Smaller caps for a quicker build.")
    args = ap.parse_args()

    settings = get_settings()
    settings.demo_cache_enabled = False  # never replay while building
    if args.fast:
        settings.run_max_places = 8
        settings.run_judge_k = 12
        settings.run_top_k = 10

    ruleset = _build_ruleset()
    print("Ruleset:", summarize_ruleset(ruleset))

    store = InMemoryRunStore()
    run_id = "demo_build"
    store.create_run(run_id)

    pipeline = RunPipeline(
        store,
        settings=settings,
        status_listener=lambda rid, st: print(f"  stage → {st.value}"),
    )
    print(f'Running real pipeline for: "{BUY_BOX}" …')
    pipeline.execute(run_id, ruleset)

    run = store.get_run(run_id)
    if run is None or run.status != RunStatus.COMPLETE:
        print(f"Build did not complete (status={run.status if run else 'missing'}).", file=sys.stderr)
        return 1

    payload = {
        "key": CACHE_KEY,
        "buy_box": BUY_BOX,
        "source_plan": [p.model_dump() for p in run.source_plan],
        "coverage": run.coverage,
        "shortlist": run.shortlist or [],
    }
    path = demo_cache.save(CACHE_KEY, payload)
    print(f"\nSaved {len(payload['shortlist'])} companies → {path}")
    print("coverage:", run.coverage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
