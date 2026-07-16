"""Live integration tests for the NATA connector (Task 9).

Two independently-gated tests:
  - test_classifier_live_qwen: hits a local Ollama qwen2.5:3b (skips if not pulled).
  - test_nata_sweep_live: runs the real apify/playwright-scraper actor against the
    live nata.com.au site (skips if APIFY_API_TOKEN is unset). Spends Apify credits.
    Uses a single small tile (state=NSW, s=water) — ~10 results = 1 page = cheap.

NOTE: the raw-fetch entry point is ``_fetch_sites`` (returns raw site dicts), not
``fetch`` (which additionally aggregates + classifier-gates into CompanyRecords).
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from sourcing.classifiers.ownership_classifier import PRIVATE, OwnershipClassifier
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
    clf = OwnershipClassifier()
    out = clf.classify(["Melbourne Pathology Pty Ltd", "NSW Health Pathology",
                        "Bureau Veritas Australia"])
    cats = {c.name: c.category for c in out}
    assert cats["Melbourne Pathology Pty Ltd"] == PRIVATE
    assert cats["NSW Health Pathology"] == "public_sector"


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
