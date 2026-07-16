"""Live integration tests for the NATA connector (Task 9).

Two independently-gated tests:
  - test_classifier_live_qwen: hits a local Ollama qwen2.5:3b (skips if not pulled).
  - test_nata_sweep_live: runs the real apify/playwright-scraper actor against the
    live nata.com.au site (skips if APIFY_API_TOKEN is unset). Spends Apify credits.
    Uses a single small tile (state=NSW, s=water) — ~10 results = 1 page = cheap.

NOTE: the raw-fetch entry point is ``_fetch_sites`` (returns raw site dicts), not
``fetch`` (which additionally aggregates + classifier-gates into CompanyRecords).

NOTE on test_classifier_live_qwen: qwen2.5:3b judgment quality is low/non-deterministic;
for reliable production classification set classifier_provider=anthropic (Claude Haiku).
This test only verifies the live plumbing.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from sourcing.classifiers.ownership_classifier import (
    CATEGORIES,
    Classification,
    OwnershipClassifier,
)
from sourcing.config import get_settings
from sourcing.connectors.nata import NATAConnector

pytestmark = pytest.mark.integration


def _qwen_available() -> bool:
    if not shutil.which("ollama"):
        return False
    out = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    return "qwen2.5:3b" in out.stdout


@pytest.mark.skipif(not _qwen_available(), reason="qwen2.5:3b not pulled")
def test_classifier_live_qwen():
    # qwen2.5:3b is non-deterministic and often mis-judges categories, so we only
    # assert the end-to-end plumbing works, not qwen's judgment.
    clf = OwnershipClassifier()
    out = clf.classify(["Melbourne Pathology Pty Ltd", "NSW Health Pathology",
                        "Bureau Veritas Australia"])
    assert len(out) == 3
    for c in out:
        assert isinstance(c, Classification)
        assert c.category in CATEGORIES
        assert isinstance(c.confidence, float)
        assert 0.0 <= c.confidence <= 1.0


@pytest.mark.skipif(not get_settings().apify_api_token, reason="APIFY_API_TOKEN not set")
def test_nata_sweep_live():
    # Spends Apify credits. One small NSW/water tile (~10 results = 1 page).
    c = NATAConnector()
    raws = c._fetch_sites({"state": "NSW", "search": "water"})
    assert isinstance(raws, list)
    if raws:
        assert any(r.get("accreditation_number") and r.get("parent_org") for r in raws)
        parents = c._group_by_parent(raws)
        assert parents
        assert parents[0]["site_count"] >= 1
