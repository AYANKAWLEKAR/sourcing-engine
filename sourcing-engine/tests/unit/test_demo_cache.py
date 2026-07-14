"""Demo-prompt cache: prompt matching + pipeline replay (offline)."""
from __future__ import annotations

from sourcing.models.run import RunStatus
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs import demo_cache
from sourcing.runs.pipeline import RunPipeline
from sourcing.runs.store import InMemoryRunStore


def test_match_prompt_variants():
    assert demo_cache.match_prompt("founder owned HVAC companies; 1-5M ebitda in all of sydney area") == "hvac_sydney"
    assert demo_cache.match_prompt("HVAC installers in NSW") == "hvac_sydney"
    assert demo_cache.match_prompt("bakeries in Perth") is None


def _sample_payload() -> dict:
    return {
        "key": "hvac_sydney",
        "buy_box": "HVAC in Sydney",
        "source_plan": [],
        "coverage": {"n_raw": 120, "n_pool": 90, "n_resolved": 45, "n_shortlist": 2},
        "shortlist": [
            {
                "record": {"legal_name": "Cool Air Co", "abn": "1" * 11},
                "s_stat": 70.0,
                "s_final": 0.7,
                "judge_fit": 0.72,
                "judge_rationale": "Strong fit.",
                "standout_signals": ["award finalist"],
            }
        ],
    }


def test_replay_serves_cache_without_running(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_cache, "CACHE_DIR", tmp_path)
    demo_cache.save("hvac_sydney", _sample_payload())

    store = InMemoryRunStore()
    store.create_run("run_x")

    class _NoSettings:
        demo_cache_enabled = True
        demo_cache_replay_seconds = 0.0

    pipeline = RunPipeline(store, settings=_NoSettings())
    # No components built (would need live services) — replay must short-circuit.
    shortlist = pipeline.execute("run_x", load_origo_ruleset(), cache_key="hvac_sydney")

    assert len(shortlist) == 1
    run = store.get_run("run_x")
    assert run.status is RunStatus.COMPLETE
    assert run.coverage.get("n_resolved") == 45
    assert run.shortlist and run.shortlist[0]["record"]["legal_name"] == "Cool Air Co"
    # The full record is persisted so the detail drawer works.
    assert store.get_company("run_x", "1" * 11) is not None
    # Every pipeline stage was stepped through (so the UI trace animates).
    seen = {h["status"] for h in run.stage_history}
    assert {"planning", "acquiring", "resolving", "enriching", "ranking", "complete"} <= seen
