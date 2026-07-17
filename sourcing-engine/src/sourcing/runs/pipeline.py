"""RunPipeline — the chained, persisted pipeline execution (next-phase plan §4.2).

One entry point chains the existing pieces, writing status to the RunStore at
every stage boundary so progress is observable:

    planning   SourceRetriever (RAG) → SourcePlan             [first prod wiring]
    acquiring  SourcingOrchestrator.fetch_all → dedup
    resolving  EntityResolver.enrich per ABN-less record → dedup by ABN
    enriching  EnrichmentNode.enrich_pool (checkpoint → store.save_company)
    ranking    rank_pool → ShortlistGate → save_shortlist
    complete   (or failed, with "{stage}: {exc!r}")

A plain sequential pipeline — the plan doc explicitly blesses this over LangGraph
("the stage boundaries are identical either way"). Components are built fresh per
``execute()`` (no shared DuckDB handles across concurrent runs) and every one is
injectable so the pipeline unit-tests offline with fakes.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.run import PIPELINE_STAGES, RunStatus
from ..rank.buybox import BuyBox

if TYPE_CHECKING:
    from ..models.filter_rule import FilterRuleset
    from ..models.ranking import RankedCompany
    from .store import RunStore


@dataclass
class PipelineComponents:
    """The stage implementations. ``build_default()`` wires the live engine."""

    registry_entries: list[Any]
    retriever: Any          # .index(registry) done; .retrieve(ruleset, k) -> plan
    orchestrator: Any       # .fetch_all(plan, buybox, max_places=) -> list[CompanyRecord]
    resolver: Any           # .enrich(record) -> record ; .asic.close() if present
    enrichment: Any         # .enrich_pool(pool, buybox, max_workers=, checkpoint=)
    ranker: Callable[..., list[RankedCompany]]   # rank_pool signature
    shortlist_gate: Any     # .apply(shortlist) -> shortlist

    @classmethod
    def build_default(cls, settings: Any = None) -> PipelineComponents:
        from ..config import get_settings
        from ..connectors.abn_bulk import ABNBulkExtractConnector
        from ..connectors.asx_listed import ASXListedConnector
        from ..connectors.grantconnect import GrantConnectBulkConnector
        from ..connectors.ipgod import IPGODConnector
        from ..connectors.website import WebsiteFetchConnector
        from ..enrichment.enrichment_node import EnrichmentNode
        from ..enrichment.entity_resolution import EntityResolver
        from ..orchestrator import SourcingOrchestrator
        from ..rag.embeddings import get_embedding_provider
        from ..rag.registry_seed import load_seed_registry
        from ..rag.retriever import SourceRetriever
        from ..rag.vector_store import InMemoryVectorStore
        from ..rank.rank import rank_pool
        from .shortlist_gate import ShortlistGate

        s = settings or get_settings()
        registry = load_seed_registry()

        retriever = SourceRetriever(InMemoryVectorStore(), get_embedding_provider(s))
        retriever.index(registry)

        # Settings-gated bulk lookups: each stays None (skipped) unless its data
        # is configured, so a default build never demands bulk files be present.
        abn_bulk = ABNBulkExtractConnector.from_settings() if s.abn_bulk_enabled else None
        ipgod = IPGODConnector.from_settings() if s.ipgod_csv_paths else None
        asx = ASXListedConnector.from_settings_if_available()
        grantconnect = GrantConnectBulkConnector.from_settings() if s.grantconnect_enabled else None
        # Persistent ABN-keyed enrichment cache (None unless cache_backend=sqlite).
        from ..enrichment.record_cache import CompanyRecordCache

        record_cache = CompanyRecordCache.from_settings()
        # Website fetch shares the process default cache (persistent when
        # cache_backend=sqlite), so repeat runs don't re-bill the same URLs.
        from ..connectors.cache import get_default_cache

        return cls(
            registry_entries=registry,
            retriever=retriever,
            orchestrator=SourcingOrchestrator(registry),
            resolver=EntityResolver(abn_bulk=abn_bulk),
            enrichment=EnrichmentNode(
                website=WebsiteFetchConnector(cache=get_default_cache()),
                ipgod=ipgod,
                asx=asx,
                grantconnect=grantconnect,
                record_cache=record_cache,
            ),
            ranker=rank_pool,
            shortlist_gate=ShortlistGate(registry, top_n=s.shortlist_gate_n),
        )


class RunPipeline:
    """Executes a confirmed ruleset through the full pipeline, persisting as it goes."""

    def __init__(
        self,
        store: RunStore,
        *,
        components: PipelineComponents | None = None,
        settings: Any = None,
        status_listener: Callable[[str, RunStatus], None] | None = None,
    ) -> None:
        from ..config import get_settings

        self._store = store
        self._components = components  # None → built fresh per execute()
        self._settings = settings or get_settings()
        self._status_listener = status_listener

    # ------------------------------------------------------------------

    def execute(
        self, run_id: str, ruleset: FilterRuleset, *, cache_key: str | None = None
    ) -> list[RankedCompany]:
        # Demo replay: serve a captured run through the same stage transitions (so
        # the UI trace still animates) instead of hitting Apify + the LLMs.
        if cache_key and getattr(self._settings, "demo_cache_enabled", True):
            from . import demo_cache

            cached = demo_cache.load(cache_key)
            if cached is not None:
                return self._replay(run_id, cached)

        stage = RunStatus.PLANNING
        try:
            comp = self._components or PipelineComponents.build_default(self._settings)
            s = self._settings
            buybox = BuyBox.from_ruleset(ruleset)

            # --- planning -------------------------------------------------
            self._set(run_id, RunStatus.PLANNING)
            if getattr(s, "run_use_all_sources", False):
                # Bypass RAG selection: plan every enabled source and let the
                # orchestrator's own gating decide what actually runs.
                from ..rag.retriever import all_sources_plan

                plan = all_sources_plan(comp.registry_entries, ruleset)
            else:
                plan = comp.retriever.retrieve(ruleset, k=s.run_plan_k)
            self._store.save_source_plan(run_id, plan)

            # --- acquiring ------------------------------------------------
            stage = RunStatus.ACQUIRING
            self._set(run_id, stage)
            raw_pool = comp.orchestrator.fetch_all(plan, buybox, max_places=s.run_max_places)
            from ..connectors.dedup import deduplicate_pre_resolution

            pool = deduplicate_pre_resolution(raw_pool)
            self._store.update_coverage(run_id, n_raw=len(raw_pool), n_pool=len(pool))

            # --- resolving ------------------------------------------------
            stage = RunStatus.RESOLVING
            self._set(run_id, stage)
            for rec in pool:
                if not rec.abn:
                    comp.resolver.enrich(rec)
            from ..connectors.dedup import deduplicate_by_abn

            pool = deduplicate_by_abn(pool)
            resolved = [r for r in pool if r.abn]
            self._store.update_coverage(run_id, n_resolved=len(resolved))

            # --- enriching ------------------------------------------------
            stage = RunStatus.ENRICHING
            self._set(run_id, stage)
            comp.enrichment.enrich_pool(
                resolved,
                buybox,
                max_workers=s.run_enrich_workers,
                checkpoint=lambda r: self._store.save_company(run_id, r),
            )

            # --- ranking --------------------------------------------------
            stage = RunStatus.RANKING
            self._set(run_id, stage)
            shortlist = comp.ranker(
                resolved, buybox, top_k=s.run_top_k, judge_k=s.run_judge_k
            )
            shortlist = comp.shortlist_gate.apply(shortlist)
            # Gate may have enriched records — re-checkpoint the shortlist.
            for rc in shortlist:
                self._store.save_company(run_id, rc.record)
            self._store.save_shortlist(run_id, shortlist)
            self._store.update_coverage(run_id, n_shortlist=len(shortlist))

            # --- complete ---------------------------------------------------
            self._set(run_id, RunStatus.COMPLETE)
            self._close(comp)
            return shortlist

        except Exception as exc:
            self._store.set_status(run_id, RunStatus.FAILED, error=f"{stage.value}: {exc!r}")
            raise

    # ------------------------------------------------------------------

    def _replay(self, run_id: str, cached: dict) -> list[RankedCompany]:
        """Replay a captured run: step every stage (with the real coverage/plan the
        run produced) so the trace animates, but serve the shortlist from cache."""
        from ..models.ranking import RankedCompany
        from ..models.source import SourcePlanItem

        pace = float(getattr(self._settings, "demo_cache_replay_seconds", 0.9))
        coverage: dict = cached.get("coverage") or {}
        plan = [SourcePlanItem(**p) for p in cached.get("source_plan") or []]
        shortlist = [RankedCompany(**rc) for rc in cached.get("shortlist") or []]

        # Which coverage counters land at which stage boundary (so numbers stream
        # in as they did on the real run rather than all at once at the end).
        stage_coverage = {
            RunStatus.ACQUIRING: ("n_raw", "n_pool"),
            RunStatus.RESOLVING: ("n_resolved",),
            RunStatus.RANKING: ("n_shortlist",),
        }

        for stage in PIPELINE_STAGES:
            self._set(run_id, stage)
            if stage is RunStatus.PLANNING and plan:
                self._store.save_source_plan(run_id, plan)
            counters = {
                k: coverage[k] for k in stage_coverage.get(stage, ()) if k in coverage
            }
            if counters:
                self._store.update_coverage(run_id, **counters)
            time.sleep(pace)

        # Persist the shortlist records so the company-detail drawer works.
        for rc in shortlist:
            self._store.save_company(run_id, rc.record)
        self._store.save_shortlist(run_id, shortlist)
        self._store.update_coverage(run_id, n_shortlist=len(shortlist))
        self._set(run_id, RunStatus.COMPLETE)
        return shortlist

    def _set(self, run_id: str, status: RunStatus) -> None:
        self._store.set_status(run_id, status)
        if self._status_listener is not None:
            self._status_listener(run_id, status)

    @staticmethod
    def _close(comp: PipelineComponents) -> None:
        # EntityResolver holds DuckDB handles via its ASIC connector and (when
        # ABN_BULK_ENABLED) the ABN bulk-extract connector.
        # Enrichment's GrantConnect connector can hold the shared local DuckDB
        # connection too, so include it in the best-effort run cleanup.
        connections = [
            getattr(comp.resolver, attr, None) for attr in ("asic", "abn_bulk")
        ]
        connections.append(getattr(comp.enrichment, "grantconnect", None))
        for conn in connections:
            close = getattr(conn, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
