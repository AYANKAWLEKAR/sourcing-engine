"""Unit tests for the connector base layer (plan §2 checkpoint).

Covers: Protocol conformance for every base class, the API rate limiter and
cache, the scrape cache, JSONP unwrapping, and the dynamic loader. All offline.
"""
from __future__ import annotations

import pytest

from sourcing.connectors import (
    AgentConnector,
    APIConnector,
    BulkConnector,
    MCPConnector,
    ScrapeConnector,
    SourceConnector,
    load_connector,
)
from sourcing.connectors.base_api import unwrap_jsonp
from sourcing.connectors.cache import InMemoryTTLCache

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeClock:
    """Controllable monotonic clock; ``sleep`` advances time."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.slept.append(d)
        self.t += d


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class FakeApifyClient:
    """Mimics the apify-client surface used by ScrapeConnector."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.call_count = 0

    def actor(self, actor_id: str):
        client = self

        class _Actor:
            def call(self, run_input: dict, **kwargs):
                client.call_count += 1
                return {"defaultDatasetId": "ds1"}

        return _Actor()

    def dataset(self, dataset_id: str):
        client = self

        class _Dataset:
            def list_items(self):
                class _Result:
                    items = client._items
                return _Result()

        return _Dataset()


# ---------------------------------------------------------------------------
# Protocol conformance — one trivial subclass of each base class
# ---------------------------------------------------------------------------

def _trivial(base):
    class _C(base):
        source_id = "trivial"

        def fetch(self, params):
            return []

        def normalize(self, raw):
            return None

        # ScrapeConnector subclasses also need build_input
        def build_input(self, params):
            return {}

    return _C


@pytest.mark.parametrize(
    "base", [BulkConnector, APIConnector, ScrapeConnector, AgentConnector, MCPConnector]
)
def test_protocol_conformance(base):
    obj = _trivial(base)()
    assert isinstance(obj, SourceConnector)


# ---------------------------------------------------------------------------
# API rate limiter
# ---------------------------------------------------------------------------

def test_api_rate_limiter_spaces_calls():
    clock = FakeClock()
    calls = {"n": 0}

    def transport(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResponse('{"ok": true}')

    class TwoPerSec(APIConnector):
        source_id = "rl"
        rate_limit_rps = 2.0  # min interval 0.5s

    c = TwoPerSec(cache=InMemoryTTLCache(clock=clock.now), clock=clock.now, sleep=clock.sleep, transport=transport)
    c._get("http://x", params={"q": 1})
    c._get("http://x", params={"q": 2})  # different params → cache miss

    assert calls["n"] == 2
    assert clock.slept == [pytest.approx(0.5)]


# ---------------------------------------------------------------------------
# API cache
# ---------------------------------------------------------------------------

def test_api_cache_hit_skips_second_request():
    calls = {"n": 0}

    def transport(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResponse('{"v": 1}')

    class Cached(APIConnector):
        source_id = "ch"
        rate_limit_rps = 0  # no limiter delay

    c = Cached(transport=transport)
    a = c._get("http://x", params={"q": 1})
    b = c._get("http://x", params={"q": 1})  # identical → from cache

    assert a == b == {"v": 1}
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Scrape cache
# ---------------------------------------------------------------------------

def test_scrape_cache_hit_skips_second_actor_run():
    fake = FakeApifyClient(items=[{"title": "Acme"}])

    class Maps(ScrapeConnector):
        source_id = "maps"

        def build_input(self, params):
            return {"q": params["q"]}

        def normalize(self, raw):
            return None

    c = Maps(cache=InMemoryTTLCache(), client=fake)
    first = c.fetch({"q": "hvac"})
    second = c.fetch({"q": "hvac"})  # identical → cached

    assert first == second == [{"title": "Acme"}]
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# JSONP
# ---------------------------------------------------------------------------

def test_jsonp_unwrap_object():
    assert unwrap_jsonp('callback({"a": 1})') == {"a": 1}


def test_jsonp_unwrap_plain_json():
    assert unwrap_jsonp('{"b": 2}') == {"b": 2}


def test_jsonp_unwrap_array_and_semicolon():
    assert unwrap_jsonp('cb([1, 2, 3]);') == [1, 2, 3]


def test_jsonp_unwrap_garbage_raises():
    import json

    with pytest.raises(json.JSONDecodeError):
        unwrap_jsonp("not json at all")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def test_loader_resolves_to_right_class():
    obj = load_connector("sourcing.connectors.asic_bulk.ASICBulkConnector", csv_path="x")
    assert isinstance(obj, BulkConnector)
    assert obj.source_id == "asic_company_dataset"


def test_loader_rejects_bad_ref():
    with pytest.raises(ValueError):
        load_connector("nodothere")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_inmemory_cache_set_get_and_expiry():
    clock = FakeClock()
    cache = InMemoryTTLCache(clock=clock.now)
    cache.set("k", {"v": 1}, ttl_seconds=10)
    assert cache.get("k") == {"v": 1}
    clock.t = 11  # advance past TTL
    assert cache.get("k") is None


def test_inmemory_cache_clear():
    cache = InMemoryTTLCache()
    cache.set("k", 1, ttl_seconds=100)
    cache.clear()
    assert cache.get("k") is None


def test_make_key_is_order_independent():
    from sourcing.connectors.cache import make_key

    assert make_key("s", {"a": 1, "b": 2}) == make_key("s", {"b": 2, "a": 1})
    assert make_key("s", {"a": 1}) != make_key("s", {"a": 2})


# ---------------------------------------------------------------------------
# ScrapeConnector — credential guard
# ---------------------------------------------------------------------------

def test_scrape_missing_token_raises(monkeypatch):
    from sourcing.config import get_settings

    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.setattr(get_settings(), "apify_api_token", "")  # clear .env-loaded token

    class S(ScrapeConnector):
        source_id = "s"

        def build_input(self, params):
            return {}

        def normalize(self, raw):
            return None

    with pytest.raises(RuntimeError, match="APIFY_API_TOKEN"):
        _ = S()._client


# ---------------------------------------------------------------------------
# MCPConnector — stub contract
# ---------------------------------------------------------------------------

def _mcp(**kwargs):
    class M(MCPConnector):
        source_id = "inven_mcp"

        def fetch(self, params):
            return []

        def normalize(self, raw):
            return None

    return M(**kwargs)


def test_mcp_stub_raises_without_caller():
    with pytest.raises(NotImplementedError):
        _mcp()._call_mcp_tool("inven", "search", {})


def test_mcp_with_injected_caller():
    seen = {}

    def caller(server, tool, args):
        seen.update({"server": server, "tool": tool, "args": args})
        return {"ok": True}

    out = _mcp(tool_caller=caller)._call_mcp_tool("inven", "search_companies", {"x": 1})
    assert out == {"ok": True}
    assert seen == {"server": "inven", "tool": "search_companies", "args": {"x": 1}}


def test_mcp_is_shortlist_gated_by_default():
    assert _mcp().gate == "shortlist_only"


# ---------------------------------------------------------------------------
# AgentConnector — fetch page + LLM-extract
# ---------------------------------------------------------------------------

def test_agent_fetch_and_extract_uses_injected_llm():
    fake_client = FakeApifyClient(items=[{"markdown": "page body"}])

    def fake_llm(prompt, content):
        return {"prompt": prompt, "content": content}

    agent = _trivial(AgentConnector)(client=fake_client, llm=fake_llm, cache=InMemoryTTLCache())
    out = agent._fetch_and_extract("http://example.com", "Extract finalists")
    assert out == {"prompt": "Extract finalists", "content": "page body"}
