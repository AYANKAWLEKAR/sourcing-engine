"""APIConnector — base class for live, rate-limited, cached HTTP sources (plan §2.3).

Concrete API connectors (ABN Lookup, AusTender, ABS) inherit from this and set
a few class attributes, then implement ``fetch`` and ``normalize``. The shared
``_get`` handles three cross-cutting concerns the plan mandates:

  1. **Rate limiting** — a token-bucket so we never exceed ``rate_limit_rps``.
  2. **Caching** — keyed by ``(source_id, request signature)``; an identical
     request within ``cache_ttl_seconds`` is served from cache, no HTTP call.
  3. **JSONP unwrap** — several ABR endpoints return ``callback({...})``; we
     strip the wrapper transparently.

Everything is injectable (clock, sleep, cache, transport) so the base layer is
fully unit-testable offline.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from .cache import Cache, get_default_cache, make_key
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

# Matches a JSONP envelope:  someCallback({...})  or  callback([...]) ;
_JSONP_RE = re.compile(r"^[^(]*\((?P<body>.*)\)\s*;?\s*$", re.DOTALL)


def unwrap_jsonp(text: str) -> Any:
    """Parse a JSON or JSONP payload into a Python object.

    Plain JSON is returned as-is. A JSONP envelope ``callback({...})`` has its
    wrapper stripped before parsing. Raises ``json.JSONDecodeError`` on garbage.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSONP_RE.match(stripped)
        if not match:
            raise
        return json.loads(match.group("body"))


class _RateLimiter:
    """Minimal token-bucket: ensures calls are spaced ≥ ``1/rps`` apart."""

    def __init__(
        self,
        rps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        now = self._clock()
        if self._last_call is not None:
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_call = now


class APIConnector:
    """Base for live HTTP connectors. Subclasses set the class attributes below.

    Class attributes (override in subclass):
        source_id:          registry id, e.g. ``"abn_lookup_api"``
        base_url:           endpoint root (may be overridden per-request)
        rate_limit_rps:     max requests/second (token bucket)
        cache_ttl_seconds:  how long a response stays fresh
        timeout_seconds:    per-request HTTP timeout
    """

    source_id: str = ""
    base_url: str = ""
    rate_limit_rps: float = 4.0
    cache_ttl_seconds: int = 7 * 24 * 3600
    timeout_seconds: float = 30.0

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        transport: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self._cache = cache if cache is not None else get_default_cache()
        self._limiter = _RateLimiter(self.rate_limit_rps, clock=clock, sleep=sleep)
        # ``transport`` lets tests inject a fake; defaults to httpx.get.
        self._transport = transport or httpx.get

    # ------------------------------------------------------------------
    # Shared HTTP entry point
    # ------------------------------------------------------------------

    def _get(
        self,
        url: str | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        """Rate-limited, cached GET that returns a parsed JSON/JSONP object.

        Returns the cached value when present; otherwise rate-limits, fetches,
        unwraps JSONP, caches, and returns. The cache key is derived from the
        full request signature so identical requests collapse.
        """
        target = url or self.base_url
        params = params or {}
        key = make_key(self.source_id, {"url": target, "params": params})

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        self._limiter.acquire()
        resp = self._transport(target, params=params, headers=headers, timeout=self.timeout_seconds)
        resp.raise_for_status()
        data = unwrap_jsonp(resp.text)

        self._cache.set(key, data, self.cache_ttl_seconds)
        return data

    # ------------------------------------------------------------------
    # Contract — subclasses implement these
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:  # pragma: no cover - abstract
        raise NotImplementedError

    def normalize(self, raw: RawRecord) -> CompanyRecord:  # pragma: no cover - abstract
        raise NotImplementedError
