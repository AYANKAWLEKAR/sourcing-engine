"""SourceRetriever — hybrid RAG over the Source Registry (plan §7).

Vector similarity (sector/intent fit over each source's capability doc) filtered
by hard constraints (field coverage, enabled, cost), with two always-enforced
invariants: the plan includes the spine and at least one category-bearing text
source.
"""
from __future__ import annotations

from ..models.filter_rule import FilterRuleset
from ..models.source import ConnectorType, CostTier, SourcePlanItem, SourceRegistryEntry
from .embeddings import EmbeddingProvider
from .vector_store import VectorHit, VectorStore

# Structured government spine (reach + hard filters).
SPINE_SOURCES: frozenset[str] = frozenset(
    {"abn_bulk_extract", "abn_lookup_api", "asic_company_dataset"}
)
# Category-bearing / descriptive-text sources (sector signal & business model).
TEXT_SOURCES: frozenset[str] = frozenset(
    {"google_maps", "yellow_pages", "industrynet", "website_fetch", "retail_pos_directory"}
)

_COST_ORDER = {CostTier.FREE: 0, CostTier.METERED: 1, CostTier.PAID: 2}


def required_fields(ruleset: FilterRuleset) -> set[str]:
    """Discovery-relevant fields the plan must try to cover (spec §5.1)."""
    return {r.field for r in ruleset.discovery_relevant_rules()}


def build_query_text(ruleset: FilterRuleset) -> str:
    """Compose a retrieval query from the ruleset's sector intent + dimensions."""
    parts: list[str] = []
    if ruleset.thesis_summary:
        parts.append(ruleset.thesis_summary)
    if ruleset.has_rule("sector_keyword_match"):
        inc = ruleset.rule("sector_keyword_match").logic.get("include", [])
        parts.extend(inc)
    if ruleset.has_rule("anzsic_code"):
        parts.extend(str(c) for c in ruleset.rule("anzsic_code").logic.get("values", []))
    if ruleset.has_rule("business_model"):
        parts.extend(ruleset.rule("business_model").logic.get("values", []))
    if ruleset.has_rule("state"):
        parts.extend(ruleset.rule("state").logic.get("values", []))
    return " ".join(parts).strip() or ruleset.name


def filter_field_coverage(hits: list[VectorHit], required: set[str]) -> list[VectorHit]:
    """Keep only sources that cover at least one required discovery field."""
    if not required:
        return list(hits)
    kept = []
    for h in hits:
        provided = set(h.meta.get("fields_provided", []))
        if provided & required:
            kept.append(h)
    return kept


class SourceRetriever:
    def __init__(self, store: VectorStore, embed: EmbeddingProvider):
        self.store = store
        self.embed = embed
        self._registry: dict[str, SourceRegistryEntry] = {}

    def index(self, registry: list[SourceRegistryEntry]) -> None:
        self._registry = {s.source_id: s for s in registry}
        vecs = self.embed.embed([s.capability_doc for s in registry])
        self.store.upsert_many(
            [(s.source_id, v, s.meta) for s, v in zip(registry, vecs, strict=True)]
        )

    def retrieve(
        self,
        ruleset: FilterRuleset,
        k: int = 8,
        max_cost_tier: CostTier | None = None,
    ) -> list[SourcePlanItem]:
        required = required_fields(ruleset)
        q = self.embed.embed([build_query_text(ruleset)])[0]

        # Pull a wide candidate set; disabled sources are excluded at the store.
        hits = self.store.query(q, k=max(k * 3, 24), filters={"enabled": True})
        score_map: dict[str, VectorHit] = {h.id: h for h in hits}

        cands = filter_field_coverage(hits, required)
        if max_cost_tier is not None:
            cands = [h for h in cands if self._within_budget(h.id, max_cost_tier)]
        cands.sort(key=lambda h: h.score, reverse=True)

        # Truncate to k, then enforce the invariants *within* the selection so a
        # low-scoring spine/text source is never sliced off after injection.
        selection = cands[:k]
        selection = self._enforce(selection, cands, score_map, SPINE_SOURCES, max_cost_tier, k)
        selection = self._enforce(selection, cands, score_map, TEXT_SOURCES, max_cost_tier, k)

        selection.sort(key=lambda h: h.score, reverse=True)
        return [self._to_plan_item(h, required) for h in selection]

    # --- invariants (plan §7.4) ---
    def _enforce(
        self,
        selection: list[VectorHit],
        cands: list[VectorHit],
        score_map: dict[str, VectorHit],
        want: frozenset[str],
        max_cost_tier: CostTier | None,
        k: int,
    ) -> list[VectorHit]:
        ids = {h.id for h in selection}
        if ids & want:
            return selection

        inject = self._pick(want, cands, score_map, max_cost_tier, ids)
        if inject is None:
            return selection

        if len(selection) >= k:
            # Drop the lowest-scored item that isn't itself satisfying an invariant.
            removable = [
                h for h in selection if h.id not in SPINE_SOURCES and h.id not in TEXT_SOURCES
            ]
            if removable:
                selection.remove(min(removable, key=lambda h: h.score))
        selection.append(inject)
        return selection

    def _pick(
        self,
        candidates: frozenset[str],
        cands: list[VectorHit],
        score_map: dict[str, VectorHit],
        max_cost_tier: CostTier | None,
        exclude: set[str],
    ) -> VectorHit | None:
        """Best available candidate of a type: prefer scored hits; else registry."""
        scored = [
            h
            for h in cands
            if h.id in candidates and h.id not in exclude and self._within_budget(h.id, max_cost_tier)
        ]
        scored += [
            score_map[c]
            for c in candidates
            if c in score_map
            and c not in exclude
            and c not in {h.id for h in scored}
            and self._within_budget(c, max_cost_tier)
        ]
        if scored:
            return max(scored, key=lambda h: h.score)
        for c in candidates:
            entry = self._registry.get(c)
            if entry and entry.enabled and c not in exclude and self._within_budget(c, max_cost_tier):
                return VectorHit(id=c, score=0.0, meta=entry.meta)
        return None

    def _within_budget(self, source_id: str, max_cost_tier: CostTier | None) -> bool:
        if max_cost_tier is None:
            return True
        entry = self._registry.get(source_id)
        if entry is None:
            return True
        return _COST_ORDER[entry.cost_tier] <= _COST_ORDER[max_cost_tier]

    # --- plan items ---
    def _to_plan_item(self, hit: VectorHit, required: set[str]) -> SourcePlanItem:
        provided = set(hit.meta.get("fields_provided", []))
        contributes = sorted(provided & required)
        tags: list[str] = []
        if hit.id in SPINE_SOURCES:
            tags.append("spine")
        if hit.id in TEXT_SOURCES:
            tags.append("text_source")

        bits = [f"sector fit {hit.score:.2f}"]
        if contributes:
            bits.append("covers " + ", ".join(contributes))
        if "spine" in tags:
            bits.append("structured spine (reach + hard filters)")
        if "text_source" in tags:
            bits.append("category-bearing text for sector signal")

        return SourcePlanItem(
            source_id=hit.id,
            connector_type=ConnectorType(hit.meta.get("connector_type", "api")),
            score=round(hit.score, 4),
            rationale="; ".join(bits),
            fields_contributed=contributes,
            cost_tier=CostTier(hit.meta.get("cost_tier", "free")),
            invariant_tags=tags,
        )
