"""Claude integration: the judge receives and can use GrantConnect evidence."""
from __future__ import annotations

import pytest

from sourcing.config import get_settings
from sourcing.llm import AnthropicLLMClient
from sourcing.models.company import CompanyRecord
from sourcing.rank.buybox import BuyBox
from sourcing.rank.judge import LLMJudge

pytestmark = pytest.mark.integration


def test_claude_judge_reads_grantconnect_evidence():
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not configured (required for Claude integration).")

    record = CompanyRecord(abn="12345678901", legal_name="Acme Advanced Manufacturing Pty Ltd")
    record.sector.category_text = ["advanced manufacturing"]
    record.moat_signals.gov_investment = True
    record.moat_signals.gov_grants_total_aud = 1_250_000
    record.moat_signals.gov_grants_count = 2
    record.moat_signals.gov_grant_programs = ["Modern Manufacturing Initiative"]
    judge = LLMJudge(
        llm=AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout,
            max_tokens=256,
        ),
        model="claude-haiku-4-5",
    )
    result = judge.judge(record, BuyBox(thesis="Australian advanced-manufacturing SMEs"))
    assert result.unavailable is False
    assert 0.0 <= result.fit <= 1.0
    assert result.rationale
