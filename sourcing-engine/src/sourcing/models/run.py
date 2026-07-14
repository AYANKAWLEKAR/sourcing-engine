"""Run contract — the persisted, observable pipeline run (next-phase plan §4.1).

A run moves through the exact stage vocabulary of §4.1:

    buybox → planning → acquiring → resolving → enriching → ranking → complete

plus ``failed`` (with an error string) from any stage. ``buybox`` is the only
interactive state — the multi-turn agent conversation happens there, over the API.
Every transition appends ``{"status": ..., "at": <iso>}`` to ``stage_history``.

``Run`` is the store snapshot / API status payload; the ORM row lives in
``tables/core.py::RunRow``.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .source import SourcePlanItem


class RunStatus(str, Enum):
    BUYBOX = "buybox"
    PLANNING = "planning"
    ACQUIRING = "acquiring"
    RESOLVING = "resolving"
    ENRICHING = "enriching"
    RANKING = "ranking"
    COMPLETE = "complete"
    FAILED = "failed"


# Ordered pipeline stages (excludes the interactive/terminal states).
PIPELINE_STAGES: tuple[RunStatus, ...] = (
    RunStatus.PLANNING,
    RunStatus.ACQUIRING,
    RunStatus.RESOLVING,
    RunStatus.ENRICHING,
    RunStatus.RANKING,
)


class Run(BaseModel):
    """Snapshot of a run — what GET /runs/{id} returns (minus the shortlist typing)."""

    run_id: str
    status: RunStatus = RunStatus.BUYBOX
    error: str | None = None
    ruleset_id: str | None = None
    label: str | None = None  # user-given name for a saved run
    source_plan: list[SourcePlanItem] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    shortlist: list[dict] | None = None  # RankedCompany dumps; None until ranked
    conversation: list[dict] = Field(default_factory=list)  # [{role, text, at}]
    stage_history: list[dict] = Field(default_factory=list)  # [{status, at}]
    created_at: str | None = None
    updated_at: str | None = None
