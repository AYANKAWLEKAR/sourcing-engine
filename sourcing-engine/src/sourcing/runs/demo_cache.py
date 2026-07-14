"""Demo-prompt cache — replay a captured run instead of re-running the pipeline.

The full pipeline (Apify scraping → ABN resolution → website+LLM enrichment → LLM
judge) takes minutes and spends credits. For canned demo prompts we capture one real
run once (``scripts/build_demo_cache.py``) and replay it: the UI still steps through
every stage with live traces, but the heavy work is served from ``data/demo_cache/``.

Matching is on the *buy-box prompt text* (deterministic — unlike the LLM-resolved
ruleset), so "founder owned HVAC companies; 1-5M ebitda in all of sydney area" and
its close variants all resolve to the same cache key.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# data/demo_cache/<key>.json, resolved relative to the package's data dir.
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "demo_cache"

# key -> predicate over the normalized prompt. Order matters (first match wins).
# Add an entry here + build its cache to make another prompt instant.
_MATCHERS: list[tuple[str, Any]] = [
    (
        "hvac_sydney",
        lambda t: "hvac" in t and ("sydney" in t or "nsw" in t),
    ),
]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def match_prompt(text: str) -> str | None:
    """Return the demo cache key whose matcher accepts ``text``, else ``None``."""
    norm = _normalize(text)
    for key, predicate in _MATCHERS:
        if predicate(norm):
            return key
    return None


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load(key: str) -> dict | None:
    """Load a captured run payload for ``key`` (``None`` if not built yet)."""
    path = cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save(key: str, payload: dict) -> Path:
    """Persist a captured run payload. Payload shape:

    ``{"key", "buy_box", "source_plan": [...], "coverage": {...}, "shortlist": [...]}``
    where ``shortlist`` is a list of ``RankedCompany.model_dump()`` dicts.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(key)
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
