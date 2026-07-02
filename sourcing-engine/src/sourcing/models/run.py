"""Run / Job contract (spec §3.5)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .source import SourcePlanItem


class RunStage(str, Enum):
    SCHEMA = "schema"
    SOURCE_SELECTION = "source_selection"
    ACQUISITION = "acquisition"
    RANKING = "ranking"
    DONE = "done"
    FAILED = "failed"


class Coverage(BaseModel):
    sources_hit: list[str] = Field(default_factory=list)
    rows_fetched: int = 0
    failures: list[str] = Field(default_factory=list)
    cost_aud: float = 0.0


class Run(BaseModel):
    run_id: str
    ruleset_id: str
    source_plan: list[SourcePlanItem] = Field(default_factory=list)
    stage: RunStage = RunStage.SCHEMA
    coverage: Coverage = Field(default_factory=Coverage)
    pool_ref: str | None = None
    results_ref: str | None = None
    created_at: str | None = None
