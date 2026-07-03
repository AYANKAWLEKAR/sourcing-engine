"""Source registry & source-plan contracts (spec §3.4, §5.4)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ConnectorType(str, Enum):
    BULK = "bulk"
    API = "api"
    MCP = "mcp"
    SCRAPE = "scrape"
    AGENT = "agent"


class CostTier(str, Enum):
    FREE = "free"
    METERED = "metered"
    PAID = "paid"


class SourceRegistryEntry(BaseModel):
    source_id: str
    connector_type: ConnectorType
    fields_provided: list[str] = Field(default_factory=list)
    sectors_covered: list[str] = Field(default_factory=list)
    geo_granularity: str | None = None  # postcode | state | national | none
    join_key: str | None = None
    cost_tier: CostTier = CostTier.FREE
    freshness: str | None = None
    reliability: str | None = None
    enabled: bool = True
    rate_limit: str | None = None
    connector_ref: str | None = None
    # "shortlist_only" → never run during full-pool discovery sweep.
    gate: str | None = None
    # Natural-language description embedded for vector retrieval (Step-1 RAG).
    capability_doc: str = ""

    @property
    def meta(self) -> dict:
        """Metadata stored alongside the vector (used for filtering at query time)."""
        return {
            "source_id": self.source_id,
            "connector_type": self.connector_type.value,
            "fields_provided": list(self.fields_provided),
            "sectors_covered": list(self.sectors_covered),
            "geo_granularity": self.geo_granularity,
            "cost_tier": self.cost_tier.value,
            "enabled": self.enabled,
        }


class SourcePlanItem(BaseModel):
    """An ordered, explainable entry in the Source Plan (spec §5.4)."""

    source_id: str
    connector_type: ConnectorType
    score: float
    rationale: str
    fields_contributed: list[str] = Field(default_factory=list)
    cost_tier: CostTier = CostTier.FREE
    invariant_tags: list[str] = Field(default_factory=list)  # e.g. ["spine"], ["text_source"]
