"""Ruleset loader unit tests (plan §8.2)."""
from __future__ import annotations

from sourcing.models.filter_rule import DiscoveryAction
from sourcing.ruleset.loader import parse_logic


def test_loader_rule_count(base_ruleset):
    # 38 CSV rows minus professional_licence_required.
    assert len(base_ruleset.rules) == 37


def test_loader_excludes_professional_licence(base_ruleset):
    assert not base_ruleset.has_rule("professional_licence_required")


def test_loader_parses_ebitda_logic(base_ruleset):
    assert base_ruleset.rule("ebitda_aud").logic == {
        "min": 1_000_000,
        "max": 15_000_000,
        "sweet_spot": [1_500_000, 10_000_000],
    }


def test_loader_derives_actions(base_ruleset):
    cases = {
        "pe_vc_backed": DiscoveryAction.EXCLUDE,
        "country": DiscoveryAction.GATE,
        "ebitda_aud": DiscoveryAction.PROXY_GATE,
        "gross_margin_pct": DiscoveryAction.DEFER_EXCLUDE,
        "state": DiscoveryAction.SCORE,
        "seller_motivation": DiscoveryAction.DEFER_ASSESS,
        "owner_addbacks_pct": DiscoveryAction.DEFER_GATE,
    }
    for field, action in cases.items():
        assert base_ruleset.rule(field).discovery_action == action, field


def test_loader_assigns_score_weights(base_ruleset):
    for r in base_ruleset.rules:
        if r.discovery_action == DiscoveryAction.SCORE:
            assert r.weight is not None and r.weight > 0
        else:
            assert r.weight is None
    assert base_ruleset.rule("state").weight == 0.10


def test_loader_keyword_logic(base_ruleset):
    logic = base_ruleset.rule("sector_keyword_match").logic
    assert "testing" in logic["include"]
    assert "retail" in logic["exclude"]


def test_parse_logic_threshold_and_boolean():
    assert parse_logic("threshold", "MIN: 5") == {"min": 5}
    assert parse_logic("threshold", "MAX: 30") == {"max": 30}
    assert parse_logic("boolean", "EQUALS: false") == {"equals": False}
    assert parse_logic("boolean", "EQUALS: true") == {"equals": True}


def test_parse_logic_match_values():
    assert parse_logic("match", "VALUES: NSW, VIC, QLD") == {"values": ["NSW", "VIC", "QLD"]}


def test_parse_logic_empty():
    assert parse_logic("range", "") == {}
