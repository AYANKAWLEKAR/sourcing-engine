"""Filter rule contracts — the unit of the ruleset (spec §3.1, §3.2)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ScreenTier(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    DISQUALIFIER = "DISQUALIFIER"
    MANUAL = "MANUAL"


class DiscoveryAction(str, Enum):
    EXCLUDE = "EXCLUDE"
    DEFER_EXCLUDE = "DEFER_EXCLUDE"
    GATE = "GATE"
    PROXY_GATE = "PROXY_GATE"
    DEFER_GATE = "DEFER_GATE"
    SCORE = "SCORE"
    DEFER_ASSESS = "DEFER_ASSESS"


# Discovery actions that materially drive sourcing & screening (spec §4.1.2 / §5.1).
DISCOVERY_RELEVANT_ACTIONS: frozenset[DiscoveryAction] = frozenset(
    {
        DiscoveryAction.GATE,
        DiscoveryAction.EXCLUDE,
        DiscoveryAction.PROXY_GATE,
        DiscoveryAction.SCORE,
    }
)


class FilterRule(BaseModel):
    field: str
    group: str
    data_type: str
    filter_type: str  # range | threshold | match | whitelist | keyword | boolean
    screen_tier: ScreenTier
    logic: dict = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    scrapeable: bool = False
    proxyable: bool = False
    discovery_action: DiscoveryAction
    weight: float | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _score_needs_weight(self) -> FilterRule:
        if self.discovery_action == DiscoveryAction.SCORE and self.weight is None:
            raise ValueError(f"SCORE rule '{self.field}' requires a weight")
        return self

    @property
    def is_discovery_relevant(self) -> bool:
        return self.discovery_action in DISCOVERY_RELEVANT_ACTIONS


class FilterRuleset(BaseModel):
    ruleset_id: str
    name: str
    base_version: str
    thesis_summary: str | None = None
    rules: list[FilterRule] = Field(default_factory=list)
    ranking_weights: dict = Field(default_factory=dict)
    created_by: str | None = None
    confirmed: bool = False

    def rule(self, field: str) -> FilterRule:
        """Return the rule for ``field`` (raises KeyError if absent)."""
        for r in self.rules:
            if r.field == field:
                return r
        raise KeyError(field)

    def has_rule(self, field: str) -> bool:
        return any(r.field == field for r in self.rules)

    def discovery_relevant_rules(self) -> list[FilterRule]:
        return [r for r in self.rules if r.is_discovery_relevant]
