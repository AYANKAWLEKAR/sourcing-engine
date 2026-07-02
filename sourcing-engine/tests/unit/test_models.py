"""Models & derivation unit tests (plan §8.2)."""
from __future__ import annotations

import itertools

import pytest

from sourcing.models.company import CompanyRecord
from sourcing.models.filter_rule import (
    DiscoveryAction,
    FilterRule,
    FilterRuleset,
    ScreenTier,
)
from sourcing.models.source import ConnectorType, SourceRegistryEntry
from sourcing.ruleset.derive import derive_discovery_action


def _rule(**kw) -> FilterRule:
    base = dict(
        field="x",
        group="g",
        data_type="int",
        filter_type="threshold",
        screen_tier=ScreenTier.SOFT,
        logic={"min": 1},
        sources=["abn"],
        scrapeable=True,
        discovery_action=DiscoveryAction.SCORE,
        weight=0.1,
    )
    base.update(kw)
    return FilterRule(**base)


def test_filterrule_validation_valid():
    r = _rule()
    assert r.discovery_action == DiscoveryAction.SCORE


def test_filterrule_score_without_weight_raises():
    with pytest.raises(ValueError, match="requires a weight"):
        _rule(discovery_action=DiscoveryAction.SCORE, weight=None)


def test_discovery_action_derivation_full_matrix():
    """Table-driven over every (tier × scrapeable × proxyable) — spec §1.1."""
    expected = {
        # DISQUALIFIER
        (ScreenTier.DISQUALIFIER, True, False): DiscoveryAction.EXCLUDE,
        (ScreenTier.DISQUALIFIER, True, True): DiscoveryAction.EXCLUDE,
        (ScreenTier.DISQUALIFIER, False, False): DiscoveryAction.DEFER_EXCLUDE,
        (ScreenTier.DISQUALIFIER, False, True): DiscoveryAction.DEFER_EXCLUDE,
        # HARD
        (ScreenTier.HARD, True, False): DiscoveryAction.GATE,
        (ScreenTier.HARD, True, True): DiscoveryAction.GATE,
        (ScreenTier.HARD, False, True): DiscoveryAction.PROXY_GATE,
        (ScreenTier.HARD, False, False): DiscoveryAction.DEFER_GATE,
        # SOFT
        (ScreenTier.SOFT, True, False): DiscoveryAction.SCORE,
        (ScreenTier.SOFT, True, True): DiscoveryAction.SCORE,
        (ScreenTier.SOFT, False, False): DiscoveryAction.DEFER_ASSESS,
        (ScreenTier.SOFT, False, True): DiscoveryAction.DEFER_ASSESS,
        # MANUAL
        (ScreenTier.MANUAL, True, False): DiscoveryAction.DEFER_ASSESS,
        (ScreenTier.MANUAL, False, False): DiscoveryAction.DEFER_ASSESS,
        (ScreenTier.MANUAL, True, True): DiscoveryAction.DEFER_ASSESS,
        (ScreenTier.MANUAL, False, True): DiscoveryAction.DEFER_ASSESS,
    }
    for tier, scr, prox in itertools.product(
        list(ScreenTier), [True, False], [True, False]
    ):
        assert derive_discovery_action(tier, scr, prox) == expected[(tier, scr, prox)], (
            tier,
            scr,
            prox,
        )


def test_filterruleset_roundtrip(base_ruleset):
    data = base_ruleset.model_dump()
    again = FilterRuleset(**data)
    assert again.model_dump() == data
    assert len(again.rules) == len(base_ruleset.rules)


def test_company_record_roundtrip():
    rec = CompanyRecord(entity_id="e1", legal_name="Acme Testing Pty Ltd")
    again = CompanyRecord(**rec.model_dump())
    assert again.entity_id == "e1"
    assert again.country == "Australia"


def test_source_registry_entry_roundtrip_and_meta():
    e = SourceRegistryEntry(
        source_id="s1",
        connector_type=ConnectorType.BULK,
        fields_provided=["country", "state"],
        capability_doc="doc",
    )
    again = SourceRegistryEntry(**e.model_dump())
    assert again.source_id == "s1"
    assert e.meta["enabled"] is True
    assert e.meta["fields_provided"] == ["country", "state"]
