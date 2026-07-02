"""Cache abstraction for connectors (plan §2.3/§2.4 — "cache every external call").

The plan specifies Redis, but Redis is an optional runtime dependency. We define
a small ``Cache`` Protocol with an in-process TTL default so connectors work
offline and in unit tests, and a Redis-backed implementation that is used
automatically when ``redis`` is installed and ``REDIS_URL`` is configured.

Keys are deterministic hashes of ``(source_id, request signature)`` so an
identical request within the TTL never hits the network twice.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol


def make_key(source_id: str, payload: Any) -> str:
    """Build a stable cache key from a source id and an arbitrary payload.

    ``payload`` is JSON-serialised with sorted keys so equal requests — in any
    dict order — collapse to the same key.
    """
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    return f"{source_id}:{digest}"


class Cache(Protocol):
    """Minimal cache interface used by the connector base classes."""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...


class InMemoryTTLCache:
    """Process-local cache with per-entry TTL. Default for offline/tests.

    Not shared across processes — fine for a single CLI run or a test. Swap in
    ``RedisCache`` for cross-process persistence.
    """

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._clock = clock

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (self._clock() + ttl_seconds, value)

    def clear(self) -> None:
        self._store.clear()


class RedisCache:
    """Redis-backed cache. Only used when ``redis`` is installed + configured.

    Values are JSON-encoded. Constructed lazily by :func:`get_default_cache`.
    """

    def __init__(self, url: str) -> None:
        import redis  # imported lazily — optional dependency

        self._client = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Any | None:
        blob = self._client.get(key)
        return json.loads(blob) if blob is not None else None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)


def get_default_cache() -> Cache:
    """Return a Redis cache if available/configured, else an in-memory cache."""
    import os

    url = os.environ.get("REDIS_URL")
    if url:
        try:
            return RedisCache(url)
        except Exception:
            # Redis not importable or not reachable — fall back to memory.
            pass
    return InMemoryTTLCache()
