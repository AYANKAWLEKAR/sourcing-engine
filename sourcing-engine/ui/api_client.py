"""OrigoClient — thin HTTP wrapper over the run API (Part D).

The Streamlit UI never touches the engine directly; it speaks to the FastAPI
surface (``sourcing.api.app``) over HTTP. One place for the base URL, timeouts,
and error handling so the app stays declarative.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("ORIGO_API_URL", "http://localhost:8000")

# The buy-box agent turn and pipeline stages block for a while; keep a generous
# read timeout so a slow agent reply doesn't surface as a client error.
DEFAULT_TIMEOUT = 120.0


class OrigoClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    # -- buy-box chat ---------------------------------------------------

    def start_run(self, message: str) -> dict[str, Any]:
        return self._post("/runs", {"message": message})

    def continue_buybox(self, run_id: str, message: str) -> dict[str, Any]:
        return self._post(f"/runs/{run_id}/buybox", {"message": message})

    # -- run status / shortlist ----------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/runs/{run_id}")

    # -- company detail -------------------------------------------------

    def get_company(self, run_id: str, abn: str) -> dict[str, Any]:
        return self._get(f"/runs/{run_id}/companies/{abn}")

    def get_company_sources(self, run_id: str, abn: str) -> dict[str, Any]:
        return self._get(f"/runs/{run_id}/companies/{abn}/sources")

    def select(self, run_id: str, abn: str) -> dict[str, Any]:
        return self._post(f"/runs/{run_id}/select", {"abn": abn})

    # -- internals ------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        resp = self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()
