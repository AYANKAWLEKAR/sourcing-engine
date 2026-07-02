"""Live integration test for the Telstra award-register AgentConnector.

Fetches one real finalist page via rag-web-browser (Apify) and extracts finalists with
the local qwen model. Needs APIFY_API_TOKEN + a running Ollama with the qwen enrich_model.
Slow (qwen extracts ~40 names on CPU) — one page only.
"""
from __future__ import annotations

import pytest

from sourcing.connectors.awards import TelstraAwardsConnector
from sourcing.connectors.cache import InMemoryTTLCache

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def finalists(require_apify_token):
    connector = TelstraAwardsConnector(cache=InMemoryTTLCache())
    raw = connector.fetch({"year": 2025, "categories": ["embracing-innovation"]})
    return connector, raw


def test_extracts_at_least_10_finalists(finalists):
    _, raw = finalists
    assert len(raw) >= 10, f"only {len(raw)} finalists extracted"


def test_finalists_have_names_and_states(finalists):
    _, raw = finalists
    named = sum(1 for r in raw if (r.get("org_name") or "").strip())
    stated = sum(1 for r in raw if r.get("state"))
    assert named == len(raw)                 # every finalist has a name
    assert stated / len(raw) > 0.7           # most carry a state


def test_normalize_sets_award_signal(finalists):
    connector, raw = finalists
    rec = connector.normalize(raw[0])
    assert rec.moat_signals.award_finalist is True
    assert rec.abn is None                   # no ABN — resolved downstream
    assert rec.award_signals and rec.award_signals[0].program == "Telstra Best of Business"
    assert rec.award_signals[0].tier == 1
    assert rec.entity_id.startswith("award:telstra:")
