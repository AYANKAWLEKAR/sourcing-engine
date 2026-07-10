"""OrigoClient unit tests — mocked HTTP via respx (offline)."""
from __future__ import annotations

import httpx
import respx

from ui.api_client import OrigoClient

BASE = "http://testserver"


def _client() -> OrigoClient:
    return OrigoClient(BASE, client=httpx.Client(base_url=BASE))


@respx.mock
def test_start_run_posts_message():
    route = respx.post(f"{BASE}/runs").mock(
        return_value=httpx.Response(201, json={"run_id": "run_1", "ruleset_confirmed": False})
    )
    out = _client().start_run("HVAC in QLD")
    assert out["run_id"] == "run_1"
    import json

    assert json.loads(route.calls.last.request.content) == {"message": "HVAC in QLD"}


@respx.mock
def test_continue_buybox_hits_run_scoped_path():
    respx.post(f"{BASE}/runs/run_1/buybox").mock(
        return_value=httpx.Response(200, json={"run_id": "run_1", "ruleset_confirmed": True})
    )
    out = _client().continue_buybox("run_1", "QLD only")
    assert out["ruleset_confirmed"] is True


@respx.mock
def test_get_run_returns_status():
    respx.get(f"{BASE}/runs/run_1").mock(
        return_value=httpx.Response(200, json={"status": "ranking", "shortlist": None})
    )
    assert _client().get_run("run_1")["status"] == "ranking"


@respx.mock
def test_get_company_and_sources_and_select():
    respx.get(f"{BASE}/runs/run_1/companies/{'1' * 11}").mock(
        return_value=httpx.Response(200, json={"record": {"legal_name": "Acme"}})
    )
    respx.get(f"{BASE}/runs/run_1/companies/{'1' * 11}/sources").mock(
        return_value=httpx.Response(200, json={"provenance": [{"field": "abn"}]})
    )
    respx.post(f"{BASE}/runs/run_1/select").mock(
        return_value=httpx.Response(200, json={"selected": True})
    )
    c = _client()
    assert c.get_company("run_1", "1" * 11)["record"]["legal_name"] == "Acme"
    assert c.get_company_sources("run_1", "1" * 11)["provenance"][0]["field"] == "abn"
    assert c.select("run_1", "1" * 11)["selected"] is True
