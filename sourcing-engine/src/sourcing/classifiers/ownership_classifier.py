"""Ownership classifier — private vs public/non-profit/multinational filter.

One narrow job: classify a NATA-accredited organisation name into one of five
ownership categories so the connector keeps only private commercial candidates.
Pluggable backend (Ollama default, Anthropic optional) via an injected
``complete`` callable; batched (10/prompt) with per-item fallback on mismatch.
"""
from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass

PRIVATE = "private_commercial"
CATEGORIES = (PRIVATE, "public_sector", "non_profit", "listed_or_multinational", "unclear")

SYSTEM_PROMPT = """You classify Australian organisations by ownership type for an acquisition sourcing engine.
For EACH organisation name, classify into ONE of:
- private_commercial: privately-owned commercial business (Pty Ltd, partnerships, sole traders, family-owned, PE/VC-backed). INCLUDE subsidiaries of larger PRIVATE companies. This is the category we want.
- public_sector: any government entity (public hospitals, state health pathology e.g. "NSW Health Pathology", departments, public universities, TAFEs, defence, CSIRO).
- non_profit: charities, industry associations, community orgs, co-operatives, professional bodies.
- listed_or_multinational: ASX-listed or subsidiaries of listed/foreign multinationals (e.g. "Bureau Veritas Australia", "SGS Australia", "Intertek"). Out of scope.
- unclear: cannot determine from the name alone.
Return ONLY a JSON array, one object per input in the SAME ORDER:
[{"category": "<one of the five>", "confidence": 0.0-1.0, "reasoning": "<short>"}]"""


@dataclass
class Classification:
    name: str
    category: str
    confidence: float
    reasoning: str


def _default_complete() -> Callable[[str], str]:
    """Build a completion callable from settings (Ollama or Anthropic)."""
    from ..config import get_settings

    s = get_settings()
    if s.classifier_provider == "anthropic":
        from ..llm import get_llm_client

        client = get_llm_client(s)

        def _complete(prompt: str) -> str:
            resp = client.chat(model=s.classifier_model, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": prompt}], format="json")
            return resp.text or ""

        return _complete

    # Ollama (default) — direct HTTP; raises on failure (caller degrades).
    import httpx

    def _complete(prompt: str) -> str:
        r = httpx.post(
            f"{s.classifier_ollama_url}/api/generate",
            json={"model": s.classifier_model,
                  "system": SYSTEM_PROMPT, "prompt": prompt,
                  "format": "json", "stream": False},
            timeout=s.classifier_timeout_seconds,
        )
        r.raise_for_status()
        return r.json().get("response", "")

    return _complete


class OwnershipClassifier:
    def __init__(self, complete: Callable[[str], str] | None = None, *, batch_size: int = 10):
        self._complete = complete or _default_complete()
        self._batch_size = batch_size

    def classify(self, names: list[str]) -> list[Classification]:
        out: list[Classification] = []
        for i in range(0, len(names), self._batch_size):
            batch = names[i : i + self._batch_size]
            out.extend(self._classify_batch(batch))
        return out

    def _classify_batch(self, batch: list[str]) -> list[Classification]:
        prompt = "Classify these organisations:\n" + "\n".join(
            f"{n+1}. {name}" for n, name in enumerate(batch)
        )
        parsed = self._call_and_parse(prompt)
        if parsed is None or len(parsed) != len(batch):
            if len(batch) > 1:
                # Order mismatch → reclassify each item individually.
                result: list[Classification] = []
                for name in batch:
                    result.extend(self._classify_batch([name]))
                return result
            parsed = parsed or [{}]
        return [self._to_classification(name, obj) for name, obj in zip(batch, parsed, strict=False)]

    def _call_and_parse(self, prompt: str) -> list[dict] | None:
        try:
            text = self._complete(prompt)
        except Exception as exc:  # noqa: BLE001 - degrade, never raise into the pipeline
            warnings.warn(f"OwnershipClassifier: completion failed: {exc}", stacklevel=2)
            return None
        return self._extract_array(text)

    @staticmethod
    def _extract_array(text: str) -> list[dict] | None:
        text = (text or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = None
            else:
                data = None
            if data is None:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if not m:
                    return None
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return OwnershipClassifier._coerce_to_list(data)

    @staticmethod
    def _coerce_to_list(data: object) -> list[dict] | None:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            categories = data.get("categories")
            if isinstance(categories, list):
                # qwen sometimes wraps the array in a {"categories": [...]} envelope.
                return categories
            if "category" in data:
                return [data]
            # Index-keyed object (e.g. {"[0]": {...}, "[1]": {...}}) — dicts preserve
            # insertion order in Python 3.7+, so this retains the model's ordering.
            return list(data.values())
        return None

    @staticmethod
    def _to_classification(name: str, obj: dict) -> Classification:
        cat = obj.get("category")
        if cat not in CATEGORIES:
            cat = "unclear"
        try:
            conf = float(obj.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return Classification(name=name, category=cat, confidence=max(0.0, min(1.0, conf)),
                              reasoning=str(obj.get("reasoning", "")))
