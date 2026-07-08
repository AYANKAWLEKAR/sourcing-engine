"""Shortlist-quality eval harness — measure whether a run is *defensible*.

Runs the real RunPipeline (in-memory store) on a fixed buy-box and reports the
metrics that matter for trust: shell rate in the top-N, sector-signal coverage,
mean judge fit, resolution-uncertainty, and PE/VC-unknown rate. Run it before and
after a quality change to prove the shortlist got better, not just different.

    python scripts/quality_eval.py
    python scripts/quality_eval.py --buybox "plumbing businesses in NSW ..."

Spends Apify + Claude credits (bounded). Reuses the demo overrides.
"""
from __future__ import annotations

import json
import os
import sys

# Bounded + persistent-cache overrides BEFORE importing sourcing.
os.environ.setdefault("ENRICH_MODEL", "claude-haiku-4-5")
os.environ.setdefault("JUDGE_MODEL", "claude-opus-4-8")
os.environ.setdefault("RUN_MAX_PLACES", "15")
os.environ.setdefault("RUN_JUDGE_K", "15")
os.environ.setdefault("RUN_TOP_K", "12")
os.environ.setdefault("CACHE_BACKEND", "sqlite")
os.environ.setdefault("SCRAPE_MAX_SEARCH_TERMS", "6")

from sourcing.config import get_settings  # noqa: E402
from sourcing.rank.quality import shortlist_quality_metrics  # noqa: E402
from sourcing.runs.manager import InlineExecutor, RunManager  # noqa: E402
from sourcing.runs.pipeline import RunPipeline  # noqa: E402
from sourcing.runs.store import InMemoryRunStore  # noqa: E402

_DEFAULT_BUYBOX = (
    "Founder-owned HVAC and air-conditioning installation/servicing businesses in "
    "Queensland. B2B focus, at least 3 years operating. Use sensible defaults and "
    "finalize the ruleset without asking questions."
)


def main() -> None:
    buybox = sys.argv[sys.argv.index("--buybox") + 1] if "--buybox" in sys.argv else _DEFAULT_BUYBOX
    s = get_settings()
    store = InMemoryRunStore()
    pipeline = RunPipeline(store, settings=s,
                           status_listener=lambda rid, st: print(f"  stage → {st.value}"))
    manager = RunManager(store, pipeline=pipeline, executor=InlineExecutor(), settings=s)

    result = manager.start_run(buybox)
    run_id, turn = result.run_id, result.turn
    guard = 0
    while not turn.ruleset.confirmed and not turn.done and guard < 6:
        guard += 1
        turn = manager.continue_buybox(run_id, "Use sensible defaults and finalize now.")
    if not turn.ruleset.confirmed:
        print("NEEDS REVIEW — agent did not confirm.")
        sys.exit(1)

    run = manager.get_run(run_id)
    if run is None or run.status.value == "failed":
        print(f"RUN FAILED: {run.error if run else 'no run'}")
        sys.exit(1)

    # Rebuild RankedCompany-ish views + the resolved pool from the store.
    resolved = [
        _AsRecord(rec) for (rid, _abn), (rec, _sel) in store._companies.items() if rid == run_id
    ]
    shortlist = [_AsRanked(rc) for rc in (run.shortlist or [])]

    metrics = shortlist_quality_metrics(shortlist, resolved)
    print("\n=== SHORTLIST QUALITY ===")
    print(f"coverage: {run.coverage}")
    print(json.dumps(metrics, indent=2))
    print("\ntop-N (S_final | judge | shell? | name):")
    for rc in shortlist:
        shell = "SHELL" if "unverified:operating_entity" in rc.record.flags else "     "
        r = rc.record
        print(f"  {rc.s_final:.3f} | {rc.judge_fit if rc.judge_fit is not None else '?':>4} | "
              f"{shell} | {(r.legal_name or '')[:44]}")


class _AsRecord:
    """Wrap a stored record dict as an attribute object for the metrics function."""

    def __init__(self, dump: dict):
        from sourcing.models.company import CompanyRecord

        self._r = CompanyRecord(**dump)

    def __getattr__(self, name):
        return getattr(self._r, name)


class _AsRanked:
    def __init__(self, rc: dict):
        self.record = _AsRecord(rc.get("record", {}))
        self.judge_fit = rc.get("judge_fit")
        self.s_final = rc.get("s_final", 0.0)


if __name__ == "__main__":
    main()
