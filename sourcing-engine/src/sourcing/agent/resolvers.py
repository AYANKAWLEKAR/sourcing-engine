"""resolve_sector / resolve_geography (plan §6.2).

Step-1 minimal resolvers: a small seed mapping in code plus an optional LLM
fallback (live only). They return correct shapes and non-empty results for seeded
inputs. Full ANZSIC/postcode tables are a later concern.
"""
from __future__ import annotations

import json
import re

from ..llm import LLMClient

# --- sector seed: keyword -> (anzsic codes, expanded keywords) ---
_SECTOR_SEED: dict[str, tuple[list[str], list[str]]] = {
    "testing": (["6925", "6920"], ["testing", "laboratory", "materials testing"]),
    "inspection": (["6925", "7299"], ["inspection", "audit", "compliance"]),
    "certification": (["6925", "6924"], ["certification", "accreditation", "compliance"]),
    "calibration": (["6925"], ["calibration", "metrology"]),
    "compliance": (["6924", "6925"], ["compliance", "regulatory", "audit"]),
    "engineering": (["6923", "6925"], ["engineering", "technical services"]),
    "environmental": (["6925", "7099"], ["environmental", "monitoring", "testing"]),
    "manufacturing": (["2400", "2500"], ["manufacturing", "production", "fabrication"]),
    "logistics": (["5290", "5101"], ["logistics", "freight", "transport"]),
    "software": (["7000", "5910"], ["software", "saas", "technology"]),
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+", text.lower())


def resolve_sector(intent_text: str, llm: LLMClient | None = None, model: str = "") -> dict:
    """Resolve free-text sector intent → ``{"anzsic_codes": [...], "keywords": [...]}``.

    Seed-first; falls back to the LLM only when no seed keyword matches and an LLM
    is supplied.
    """
    anzsic: list[str] = []
    keywords: list[str] = []
    for tok in _tokens(intent_text):
        if tok in _SECTOR_SEED:
            codes, kws = _SECTOR_SEED[tok]
            anzsic.extend(codes)
            keywords.extend(kws)

    if not keywords and llm is not None:
        anzsic, keywords = _llm_resolve_sector(intent_text, llm, model)

    return {
        "anzsic_codes": _dedupe(anzsic),
        "keywords": _dedupe(keywords),
    }


# --- geography seed: state -> postcode range (lo, hi) ---
_STATE_RANGES: dict[str, tuple[int, int]] = {
    "NSW": (2000, 2999),
    "ACT": (2600, 2618),
    "VIC": (3000, 3999),
    "QLD": (4000, 4999),
    "SA": (5000, 5799),
    "WA": (6000, 6797),
    "TAS": (7000, 7799),
    "NT": (800, 899),
}

_REGION_TO_STATE: dict[str, str] = {
    "sydney": "NSW",
    "melbourne": "VIC",
    "brisbane": "QLD",
    "queensland": "QLD",
    "gold coast": "QLD",
    "perth": "WA",
    "adelaide": "SA",
    "hobart": "TAS",
    "darwin": "NT",
    "canberra": "ACT",
}


def resolve_geography(states: list[str] | None = None, regions: list[str] | None = None) -> dict:
    """Resolve states/regions → ``{"states": [...], "ranges": [...], "postcodes": [...]}``."""
    resolved_states: list[str] = []
    for s in states or []:
        key = s.strip().upper()
        if key in _STATE_RANGES:
            resolved_states.append(key)
    for r in regions or []:
        st = _REGION_TO_STATE.get(r.strip().lower())
        if st:
            resolved_states.append(st)

    resolved_states = _dedupe(resolved_states)
    ranges = [[*_STATE_RANGES[s]] for s in resolved_states]
    # A representative sample of postcodes per state (full enumeration is wasteful).
    postcodes: list[str] = []
    for lo, hi in ranges:
        postcodes.extend(f"{p:04d}" for p in (lo, lo + 1, (lo + hi) // 2, hi))
    return {
        "states": resolved_states,
        "ranges": ranges,
        "postcodes": _dedupe(postcodes),
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in items:
        seen.setdefault(i, None)
    return list(seen)


def _llm_resolve_sector(intent_text: str, llm: LLMClient, model: str) -> tuple[list[str], list[str]]:
    system = (
        "You map an acquisition sector description to Australian ANZSIC codes and "
        "search keywords. Reply with ONLY a JSON object: "
        '{"anzsic_codes": ["..."], "keywords": ["..."]}.'
    )
    resp = llm.chat(model=model, system=system, messages=[{"role": "user", "content": intent_text}])
    try:
        match = re.search(r"\{.*\}", resp.text, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
        return (
            [str(c) for c in data.get("anzsic_codes", [])],
            [str(k) for k in data.get("keywords", [])],
        )
    except (json.JSONDecodeError, AttributeError, TypeError):
        return [], []
