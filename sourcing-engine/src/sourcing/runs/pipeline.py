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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.run import RunStatus
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
        from ..connectors.cache import InMemoryTTLCache
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

        return cls(
            registry_entries=registry,
            retriever=retriever,
            orchestrator=SourcingOrchestrator(registry),
            resolver=EntityResolver(),
            enrichment=EnrichmentNode(
                website=WebsiteFetchConnector(cache=InMemoryTTLCache())
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

    def execute(self, run_id: str, ruleset: FilterRuleset) -> list[RankedCompany]:
        stage = RunStatus.PLANNING
        try:
            comp = self._components or PipelineComponents.build_default(self._settings)
            s = self._settings
            buybox = BuyBox.from_ruleset(ruleset)

            # --- planning -------------------------------------------------
            self._set(run_id, RunStatus.PLANNING)
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

    def _set(self, run_id: str, status: RunStatus) -> None:
        self._store.set_status(run_id, status)
        if self._status_listener is not None:
            self._status_listener(run_id, status)

    @staticmethod
    def _close(comp: PipelineComponents) -> None:
        # EntityResolver holds a DuckDB handle via its ASIC connector.
        asic = getattr(comp.resolver, "asic", None)
        close = getattr(asic, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
