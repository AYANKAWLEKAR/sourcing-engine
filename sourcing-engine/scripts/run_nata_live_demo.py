"""Run and capture a real NATA → ABN/ASIC → enrichment → ranking demo.

The pipeline is production-shaped but uses an explicit NATA-only plan, avoiding
unrelated discovery connectors while exercising every pipeline stage.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sourcing.config import get_settings
from sourcing.models.source import ConnectorType, CostTier, SourcePlanItem
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.pipeline import PipelineComponents, RunPipeline
from sourcing.runs.store import InMemoryRunStore


class NATAOnlyRetriever:
    def retrieve(self, _ruleset, k: int = 8) -> list[SourcePlanItem]:
        return [SourcePlanItem(
            source_id="nata_accreditation",
            connector_type=ConnectorType.SCRAPE,
            score=1.0,
            rationale="The synthetic buy-box explicitly requires NATA accreditation.",
            fields_contributed=["moat_signals.nata_accreditation"],
            cost_tier=CostTier.METERED,
        )]


def main() -> None:
    path = Path("NATA-LIVE-PIPELINE-DEMO.json")
    try:
        settings = get_settings()
        settings.demo_cache_enabled = False
        settings.run_use_all_sources = False
        settings.run_top_k = 10
        settings.run_judge_k = 10
        settings.run_enrich_workers = 1
        # The default local qwen classifier is intentionally cheap but can miss
        # a multinational subsidiary from its name alone. This live demonstration
        # uses the configured higher-quality provider for the private-only gate.
        settings.classifier_provider = "anthropic"
        settings.classifier_model = settings.enrich_model

        ruleset = load_origo_ruleset()
        ruleset.ruleset_id = "live-nata-water-nsw"
        ruleset.name = "Live NATA water-testing demonstration"
        ruleset.thesis_summary = (
            "Find a privately owned, NATA-accredited NSW water-testing laboratory, "
            "established for at least five years, serving B2B customers."
        )
        ruleset.confirmed = True
        ruleset.rule("sector_keyword_match").logic = {"include": ["water"]}
        ruleset.rule("state").logic = {"values": ["NSW"]}

        store = InMemoryRunStore()
        run_id = "live-nata-water-nsw"
        store.create_run(run_id)
        store.save_ruleset(ruleset)
        store.attach_ruleset(run_id, ruleset.ruleset_id)

        components = PipelineComponents.build_default(settings)
        components.retriever = NATAOnlyRetriever()
        shortlist = RunPipeline(store, components=components, settings=settings).execute(run_id, ruleset)
        run = store.get_run(run_id)
        companies = [
            {"abn": abn, "selected": selected, "record": record}
            for (rid, abn), (record, selected) in store._companies.items()
            if rid == run_id
        ]
        path.write_text(json.dumps({
            "executed_at": datetime.now(UTC).isoformat(),
            "synthetic_prompt": ruleset.thesis_summary,
            "live_services": [
                "NATA register via Apify Playwright",
                "ABN Lookup API",
                "ASIC local spine",
                "AusTender",
                "Anthropic enrichment and ranking judge",
                "Anthropic ownership classification",
            ],
            "status": run.status.value,
            "coverage": run.coverage,
            "source_plan": [item.model_dump(mode="json") for item in run.source_plan],
            "resolved_and_enriched_companies": companies,
            "shortlist": [item.model_dump(mode="json") for item in shortlist],
        }, indent=2, default=str) + "\n")
    except BaseException as exc:
        path.write_text(json.dumps({"status": "failed", "error": repr(exc)}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
