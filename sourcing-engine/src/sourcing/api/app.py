"""FastAPI app — the run API surface (next-phase plan §4.3).

Handlers are plain ``def`` (not ``async``): every underlying call blocks (Ollama
agent turn ≈1 min on CPU, Postgres, Apify), so FastAPI's threadpool is the right
concurrency model. "Async + polled" is satisfied because the pipeline runs in the
RunManager's executor and clients poll ``GET /runs/{id}``.

Run with ONE worker only (``python cli.py serve``) — buy-box agent sessions are
held in-process; a multi-worker deployment would strand conversations.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from .schemas import (
    BuyBoxReply,
    CompanyResponse,
    RunStatusResponse,
    SelectRequest,
    SelectResponse,
    SourcesResponse,
    StartRunRequest,
)

if TYPE_CHECKING:
    from ..runs.manager import RunManager


def _default_manager() -> RunManager:
    from ..runs.manager import RunManager
    from ..runs.store import PostgresRunStore

    return RunManager(PostgresRunStore())


def create_app(manager: RunManager | None = None) -> FastAPI:
    app = FastAPI(
        title="Origo Sourcing Engine",
        description="Buy box in, persisted ranked shortlist out. Poll GET /runs/{run_id}.",
        version="0.1.0",
    )
    # Lazy default so importing the module (e.g. for TestClient) needs no DB.
    _manager: list[RunManager | None] = [manager]

    def mgr() -> RunManager:
        if _manager[0] is None:
            _manager[0] = _default_manager()
        return _manager[0]

    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        """Redirect the root to the Streamlit analyst UI (see `serve --ui`)."""
        return RedirectResponse(os.environ.get("UI_URL", "http://localhost:8501"))

    @app.post("/runs", response_model=BuyBoxReply, status_code=201)
    def start_run(body: StartRunRequest) -> BuyBoxReply:
        result = mgr().start_run(body.message)
        run = mgr().get_run(result.run_id)
        return BuyBoxReply(
            run_id=result.run_id,
            status=run.status.value if run else "buybox",
            reply=result.turn.text,
            agent_done=result.turn.done,
            needs_review=result.turn.needs_review,
            ruleset_confirmed=result.turn.ruleset.confirmed,
        )

    @app.post("/runs/{run_id}/buybox", response_model=BuyBoxReply)
    def continue_buybox(run_id: str, body: StartRunRequest) -> BuyBoxReply:
        try:
            turn = mgr().continue_buybox(run_id, body.message)
        except KeyError:
            raise HTTPException(404, f"unknown run {run_id}") from None
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        run = mgr().get_run(run_id)
        return BuyBoxReply(
            run_id=run_id,
            status=run.status.value if run else "buybox",
            reply=turn.text,
            agent_done=turn.done,
            needs_review=turn.needs_review,
            ruleset_confirmed=turn.ruleset.confirmed,
        )

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    def get_run(run_id: str) -> RunStatusResponse:
        run = mgr().get_run(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run {run_id}")
        return RunStatusResponse(
            run_id=run.run_id,
            status=run.status.value,
            error=run.error,
            ruleset_id=run.ruleset_id,
            source_plan=[p.model_dump() for p in run.source_plan],
            coverage=run.coverage,
            shortlist=run.shortlist,
            stage_history=run.stage_history,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @app.get("/runs/{run_id}/companies/{abn}", response_model=CompanyResponse)
    def get_company(run_id: str, abn: str) -> CompanyResponse:
        found = mgr().get_company(run_id, abn)
        if found is None:
            raise HTTPException(404, f"company {abn} not in run {run_id}")
        record, selected = found
        return CompanyResponse(run_id=run_id, abn=abn, selected=selected, record=record)

    @app.get("/runs/{run_id}/companies/{abn}/sources", response_model=SourcesResponse)
    def get_company_sources(run_id: str, abn: str) -> SourcesResponse:
        found = mgr().get_company(run_id, abn)
        if found is None:
            raise HTTPException(404, f"company {abn} not in run {run_id}")
        record, _ = found
        return SourcesResponse(run_id=run_id, abn=abn, provenance=record.provenance)

    @app.post("/runs/{run_id}/select", response_model=SelectResponse)
    def select_company(run_id: str, body: SelectRequest) -> SelectResponse:
        if mgr().get_run(run_id) is None:
            raise HTTPException(404, f"unknown run {run_id}")
        if not mgr().select(run_id, body.abn):
            raise HTTPException(404, f"company {body.abn} not in run {run_id}")
        return SelectResponse(run_id=run_id, abn=body.abn, selected=True)

    return app


# Module-level app for `uvicorn sourcing.api.app:app` / `python cli.py serve`.
app = create_app()
