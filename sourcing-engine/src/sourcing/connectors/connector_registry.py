"""ConnectorRegistry — process-level singleton for connector instances (audit Fix 17).

``load_connector()`` creates a new stateful instance on every call.  For a run
that calls the ASIC connector 100 times this produces 100 DuckDB connections,
100 in-memory caches, and duplicates rate-limiter state.

``ConnectorRegistry`` caches instances keyed by ``connector_ref`` so the same
connector is instantiated at most once per process.  Callers that need isolation
(e.g. tests) call ``ConnectorRegistry.get().clear()`` or create a fresh
``ConnectorRegistry()`` directly.
"""
from __future__ import annotations

import threading
from typing import Any


class ConnectorRegistry:
    """Thread-safe cache: ``connector_ref`` → connector instance."""

    # Process-level singleton.
    _instance: ConnectorRegistry | None = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> ConnectorRegistry:
        """Return the process-level singleton, creating it on first call."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = ConnectorRegistry()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Replace the singleton with a fresh empty registry (test helper)."""
        with cls._singleton_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def get_or_create(self, connector_ref: str, **kwargs: Any) -> Any:
        """Return the cached connector for ``connector_ref``, creating it if absent.

        Extra ``kwargs`` are forwarded to the constructor on first creation only.
        Subsequent calls ignore ``kwargs`` (the cached instance is returned as-is).
        """
        with self._lock:
            if connector_ref not in self._store:
                from .loader import load_connector
                self._store[connector_ref] = load_connector(connector_ref, **kwargs)
            return self._store[connector_ref]

    def clear(self) -> None:
        """Evict all cached connectors (use in tests or between runs)."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
