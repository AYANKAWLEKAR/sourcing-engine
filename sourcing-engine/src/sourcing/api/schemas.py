"""Request/response shapes for the run API (plan §4.3).

These are the contract the analyst UI (Part D) builds against.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..models.company import CompanyRecord, Provenance


class StartRunRequest(BaseModel):
    message: str = Field(min_length=1, description="The natural-language buy box.")


class BuyBoxReply(BaseModel):
    """Returned by POST /runs and POST /runs/{id}/buybox."""

    run_id: str
    status: str
    reply: str = ""                  # the agent's text (question or confirmation)
    agent_done: bool = False
    needs_review: bool = False       # question cap hit without confirmation
    ruleset_confirmed: bool = False  # True → the pipeline has been launched


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    error: str | None = None
    ruleset_id: str | None = None
    source_plan: list[dict] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    shortlist: list[dict] | None = None  # RankedCompany dumps; null until ranked
    stage_history: list[dict] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class CompanyResponse(BaseModel):
    run_id: str
    abn: str
    selected: bool
    record: CompanyRecord


class SourcesResponse(BaseModel):
    """Per-field source/confidence breakdown — the provenance receipts."""

    run_id: str
    abn: str
    provenance: list[Provenance]


class SelectRequest(BaseModel):
    abn: str = Field(min_length=11, max_length=11)


class SelectResponse(BaseModel):
    run_id: str
    abn: str
    selected: bool
