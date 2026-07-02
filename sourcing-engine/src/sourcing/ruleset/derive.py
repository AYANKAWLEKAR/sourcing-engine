"""Discovery-action derivation (spec §1.1, plan §5.3).

The single source of truth for mapping (screen_tier × scrapeable × proxyable)
to a discovery action. Heavily unit-tested — every combination is covered.
"""
from __future__ import annotations

from ..models.filter_rule import DiscoveryAction, ScreenTier


def derive_discovery_action(
    tier: ScreenTier,
    scrapeable: bool,
    proxyable: bool = False,
    filter_type: str | None = None,
) -> DiscoveryAction:
    """Derive a rule's discovery action.

    | tier         | scrapeable | proxyable | action        |
    |--------------|------------|-----------|---------------|
    | DISQUALIFIER | yes        | -         | EXCLUDE       |
    | DISQUALIFIER | no         | -         | DEFER_EXCLUDE |
    | HARD         | yes        | -         | GATE          |
    | HARD         | no         | yes       | PROXY_GATE    |
    | HARD         | no         | no        | DEFER_GATE    |
    | SOFT         | yes        | -         | SCORE         |
    | SOFT         | no         | -         | DEFER_ASSESS  |
    | MANUAL       | -          | -         | DEFER_ASSESS  |
    """
    if tier == ScreenTier.DISQUALIFIER:
        return DiscoveryAction.EXCLUDE if scrapeable else DiscoveryAction.DEFER_EXCLUDE

    if tier == ScreenTier.HARD:
        if scrapeable:
            return DiscoveryAction.GATE
        if proxyable:
            return DiscoveryAction.PROXY_GATE
        return DiscoveryAction.DEFER_GATE

    if tier == ScreenTier.SOFT:
        return DiscoveryAction.SCORE if scrapeable else DiscoveryAction.DEFER_ASSESS

    # MANUAL (or any other tier) is always deferred to the IM/call stage.
    return DiscoveryAction.DEFER_ASSESS
