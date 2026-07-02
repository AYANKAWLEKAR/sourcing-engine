"""ScrapeConnector — base class for managed Apify-actor sources (plan §2.4).

Concrete scrape connectors (Google Maps, Yellow Pages, Website text, LinkedIn,
ABN-Apify fallback) inherit from this, set ``actor_id`` and ``cache_ttl_seconds``,
and implement ``build_input(params)`` and ``normalize(raw)``.

``_run_actor`` calls the actor via ``apify-client`` and returns its dataset items,
with a cache keyed by ``(actor_id, params signature)`` so an identical run inside
the TTL is served from cache instead of re-billing the actor.

The ``apify-client`` import is lazy: this module loads without the dependency so
the connector hierarchy and offline unit tests (which inject a fake client) work
even when Apify isn't installed/configured. ``_client`` raises a clear error only
if a real actor run is attempted without the dependency or ``APIFY_API_TOKEN``.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .cache import Cache, get_default_cache, make_key
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord


class ScrapeConnector:
    """Base for Apify-actor connectors.

    Class attributes (override in subclass):
        source_id:          registry id, e.g. ``"google_maps"``
        actor_id:           Apify actor, e.g. ``"compass/crawler-google-places"``
        cache_ttl_seconds:  how long a run's results stay fresh
        gate:               optional ``"shortlist_only"`` to block full-pool runs
    """

    source_id: str = ""
    actor_id: str = ""
    cache_ttl_seconds: int = 30 * 24 * 3600
    gate: str | None = None

    def __init__(self, *, cache: Cache | None = None, client: Any = None) -> None:
        self._cache = cache if cache is not None else get_default_cache()
        self._injected_client = client  # tests pass a fake here

    @property
    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        from ..config import get_settings

        token = get_settings().apify_api_token or os.environ.get("APIFY_API_TOKEN")
        if not token:
            raise RuntimeError(
                f"{self.source_id}: APIFY_API_TOKEN is not set. "
                "Add it to .env to run Apify actors."
            )
        try:
            from apify_client import ApifyClient
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "apify-client is not installed. `pip install apify-client` to run scrape connectors."
            ) from exc
        return ApifyClient(token)

    def _run_actor(self, actor_input: dict) -> list[dict]:
        """Run the actor (or serve from cache) and return its dataset items."""
        key = make_key(self.source_id, {"actor": self.actor_id, "input": actor_input})
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # logger=None disables the actor's streamed-log background thread (noisy,
        # and it raises a cosmetic timeout warning on run completion).
        run = self._client.actor(self.actor_id).call(run_input=actor_input, logger=None)
        # apify-client 3.x returns a typed Run model (``default_dataset_id``);
        # older clients / test fakes return a dict (``defaultDatasetId``).
        dataset_id = getattr(run, "default_dataset_id", None)
        if dataset_id is None and isinstance(run, dict):
            dataset_id = run["defaultDatasetId"]
        items = self._client.dataset(dataset_id).list_items().items

        self._cache.set(key, items, self.cache_ttl_seconds)
        return items

    def fetch(self, params: dict) -> list[RawRecord]:
        """Default fetch: build actor input, run it, return raw items.

        Subclasses usually keep this and just implement ``build_input``/``normalize``.
        """
        return self._run_actor(self.build_input(params))

    # ------------------------------------------------------------------
    # Contract — subclasses implement these
    # ------------------------------------------------------------------

    def build_input(self, params: dict) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def normalize(self, raw: RawRecord) -> CompanyRecord:  # pragma: no cover - abstract
        raise NotImplementedError
