"""Full synthetic multi-turn run proving GrantConnect survives to the shortlist."""
from __future__ import annotations

import json

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.connectors.grantconnect import GrantConnectBulkConnector
from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.llm import LLMResponse, ScriptedLLMClient, ToolCall
from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.models.source import ConnectorType, CostTier, SourcePlanItem
from sourcing.rank.judge import LLMJudge
from sourcing.rank.rank import rank_pool
from sourcing.ruleset.loader import load_origo_ruleset
from sourcing.runs.manager import InlineExecutor, RunManager
from sourcing.runs.pipeline import PipelineComponents, RunPipeline
from sourcing.runs.store import InMemoryRunStore


class _Settings:
    demo_cache_enabled = False
    run_plan_k = 4
    run_max_places = 5
    run_enrich_workers = 1
    run_top_k = 3
    run_judge_k = 5
    shortlist_gate_n = 3


class _Retriever:
    def retrieve(self, ruleset, k):
        return [
            SourcePlanItem(
                source_id="google_maps",
                connector_type=ConnectorType.SCRAPE,
                score=1.0,
                rationale="synthetic discovery",
                cost_tier=CostTier.FREE,
            )
        ]


class _Orchestrator:
    def fetch_all(self, plan, buybox, *, max_places):
        candidate = CompanyRecord(
            entity_id="maps:acme-robotics",
            legal_name="Acme Robotics Pty Ltd",
            location=Location(state="QLD", postcode="4000"),
            business_model="B2B",
        )
        candidate.sector.category_text = ["manufacturing"]
        candidate.sector.keyword_hits = ["manufacturing", "production"]
        candidate.sector.keyword_density = 1.0
        candidate.provenance.append(Provenance(field="name", source="synthetic", confidence=1.0))
        return [candidate]


class _Resolver:
    asic = None
    abn_bulk = None

    def enrich(self, record):
        record.abn = "12345678901"
        record.resolution_confidence = 1.0
        return record


class _NoopAusTender:
    def enrich_record(self, record):
        return record


class _NoopSignals:
    def extract(self, record, buybox):
        return record


class _JudgeLLM:
    def chat(self, *args, **kwargs):
        return LLMResponse(
            text=json.dumps(
                {
                    "fit": 0.85,
                    "rationale": "manufacturing target with verified Commonwealth grant evidence",
                    "standout_signals": [],
                }
            )
        )


class _Gate:
    def apply(self, shortlist):
        return shortlist


def _staged_connector(tmp_path) -> GrantConnectBulkConnector:
    (tmp_path / "awards.csv").write_text(
        "Recipient Name,Recipient ABN,Grant Program,Grant Award Value,Grant Award Date,Recipient State\n"
        "Acme Robotics Pty Ltd,12345678901,Modern Manufacturing Initiative,1250000,15/06/2025,QLD\n",
        encoding="latin-1",
    )
    (tmp_path / "sources.yaml").write_text(
        "sources:\n"
        "  - source_dataset: synthetic-industry\n"
        "    granting_agency: Department of Industry, Science and Resources\n"
        "    file: awards.csv\n",
        encoding="utf-8",
    )
    connector = GrantConnectBulkConnector(
        db_path=tmp_path / "bulk.duckdb", sources_path=tmp_path / "sources.yaml", raw_dir=tmp_path / "raw"
    )
    connector.ensure_loaded()
    return connector


def test_multiturn_run_reaches_ranked_shortlist_with_grant_signal(tmp_path):
    grants = _staged_connector(tmp_path)
    components = PipelineComponents(
        registry_entries=[],
        retriever=_Retriever(),
        orchestrator=_Orchestrator(),
        resolver=_Resolver(),
        enrichment=EnrichmentNode(
            austender=_NoopAusTender(), signal_extractor=_NoopSignals(), grantconnect=grants
        ),
        ranker=lambda pool, buybox, **kwargs: rank_pool(
            pool, buybox, judge=LLMJudge(llm=_JudgeLLM(), model="synthetic"), **kwargs
        ),
        shortlist_gate=_Gate(),
    )
    store = InMemoryRunStore()
    pipeline = RunPipeline(store, components=components, settings=_Settings())
    agent_responses = ScriptedLLMClient(
        [
            LLMResponse(text="Which state should I target?"),
            LLMResponse(
                text="QLD manufacturing is resolved and ready.",
                tool_calls=[
                    ToolCall(name="resolve_sector", arguments={"intent_text": "manufacturing"}),
                    ToolCall(name="resolve_geography", arguments={"states": ["QLD"]}),
                    ToolCall(name="finalize_ruleset", arguments={}),
                ],
            ),
        ]
    )
    manager = RunManager(
        store,
        pipeline=pipeline,
        agent_factory=lambda: BuyBoxAgent(
            llm=agent_responses, base_ruleset=load_origo_ruleset(), model="synthetic", max_questions=3
        ),
        executor=InlineExecutor(),
        settings=_Settings(),
    )

    started = manager.start_run("Find founder-owned manufacturing firms")
    assert started.turn.done is False
    assert manager.has_session(started.run_id)

    completed = manager.continue_buybox(started.run_id, "Queensland only; proceed.")
    assert completed.ruleset.confirmed is True
    run = manager.get_run(started.run_id)
    assert run is not None and run.status.value == "complete"
    assert [h["status"] for h in run.stage_history] == [
        "buybox", "planning", "acquiring", "resolving", "enriching", "ranking", "complete"
    ]

    company, _ = store.get_company(started.run_id, "12345678901")
    assert company.moat_signals.gov_investment is True
    assert company.moat_signals.gov_grants_total_aud == 1_250_000
    assert company.moat_signals.gov_grant_programs == ["Modern Manufacturing Initiative"]
    assert "$1,250,000 Commonwealth grants" in run.shortlist[0]["standout_signals"]
