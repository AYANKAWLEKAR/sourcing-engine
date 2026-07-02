"""BuyBox — the resolved buy-box criteria the ranker/screener/extractor consume.

A thin view over a confirmed ``FilterRuleset`` that pulls out the handful of
fields enrichment + ranking need, with tolerant extraction (the ruleset encodes
criteria as per-field ``logic`` dicts). Tests construct a ``BuyBox`` directly;
production derives one with :meth:`BuyBox.from_ruleset`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.filter_rule import FilterRuleset

_EXCLUDE = "EXCLUDE"


@dataclass
class BuyBox:
    thesis: str = ""
    sector_keywords: list[str] = field(default_factory=list)
    sector_exclude_keywords: list[str] = field(default_factory=list)
    anzsic: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    target_models: list[str] = field(default_factory=list)  # e.g. ["B2B"]
    min_years: int | None = None
    ebitda_min: float | None = None
    ebitda_max: float | None = None
    exclude_listed: bool = True
    exclude_pe_vc: bool = True

    @classmethod
    def from_ruleset(cls, ruleset: FilterRuleset) -> BuyBox:
        def logic(field_name: str) -> dict:
            return ruleset.rule(field_name).logic if ruleset.has_rule(field_name) else {}

        sector = logic("sector_keyword_match")
        excludes = list(sector.get("exclude") or [])
        excludes += list(logic("sector_exclude_match").get("values") or [])
        excludes += list(logic("sector_exclude_match").get("exclude") or [])

        def is_exclude(field_name: str) -> bool:
            return (
                ruleset.has_rule(field_name)
                and ruleset.rule(field_name).discovery_action.value == _EXCLUDE
            )

        return cls(
            thesis=ruleset.thesis_summary or ruleset.name,
            sector_keywords=list(sector.get("include") or []),
            sector_exclude_keywords=excludes,
            anzsic=list(logic("anzsic_code").get("values") or []),
            states=[s.upper() for s in (logic("state").get("values") or [])],
            target_models=[m.upper() for m in (logic("business_model").get("values") or [])],
            min_years=logic("years_operating").get("min"),
            ebitda_min=logic("ebitda_aud").get("min"),
            ebitda_max=logic("ebitda_aud").get("max"),
            exclude_listed=is_exclude("listed_entity"),
            exclude_pe_vc=is_exclude("pe_vc_backed"),
        )

    # Convenience text used to seed the semantic similarity query.
    def query_text(self) -> str:
        parts = [self.thesis, *self.sector_keywords, *self.anzsic, *self.states]
        return " ".join(p for p in parts if p)
