"""Buy-Box agent tools: schemas (Ollama function-calling format) + handlers (plan §6.2).

A :class:`RulesetEditor` wraps the working ruleset and applies the side effects of
each tool call, tracking what has been resolved so ``finalize_ruleset`` can decide
whether the ruleset is complete.
"""
from __future__ import annotations

from ..llm import LLMClient
from ..models.filter_rule import FilterRuleset
from . import resolvers

# Tool schemas in the Ollama / OpenAI function-calling format.
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "update_ruleset",
            "description": "Override a single rule's logic in the working ruleset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "Rule field name, e.g. 'ebitda_aud'."},
                    "logic": {"type": "object", "description": "New structured logic dict for the rule."},
                },
                "required": ["field", "logic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_sector",
            "description": "Resolve free-text sector intent to ANZSIC codes and keywords, writing them into the ruleset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_text": {"type": "string", "description": "Sector description, e.g. 'testing and certification services'."},
                },
                "required": ["intent_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_geography",
            "description": "Resolve target states/regions to postcode ranges, writing them into the ruleset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "states": {"type": "array", "items": {"type": "string"}, "description": "State codes, e.g. ['QLD','NSW']."},
                    "regions": {"type": "array", "items": {"type": "string"}, "description": "City/region names, e.g. ['Brisbane']."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_ruleset",
            "description": "Validate the working ruleset and mark it confirmed. Fails if sector or geography is unresolved.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class FinalizeError(ValueError):
    """Raised when finalize_ruleset is called on an incomplete ruleset."""


class RulesetEditor:
    """Applies tool side effects to the working ruleset and tracks resolution state."""

    def __init__(self, ruleset: FilterRuleset, llm: LLMClient | None = None, model: str = ""):
        self.ruleset = ruleset
        self.llm = llm
        self.model = model
        self.postcodes: list[str] = []
        self.states: list[str] = []

    # --- tool handlers ---
    def update_ruleset(self, field: str, logic: dict) -> dict:
        if not self.ruleset.has_rule(field):
            return {"ok": False, "error": f"unknown field '{field}'"}
        rule = self.ruleset.rule(field)
        rule.logic = dict(logic)
        return {"ok": True, "field": field, "logic": rule.logic}

    def resolve_sector(self, intent_text: str) -> dict:
        result = resolvers.resolve_sector(intent_text, llm=self.llm, model=self.model)
        if self.ruleset.has_rule("anzsic_code") and result["anzsic_codes"]:
            self.ruleset.rule("anzsic_code").logic = {"values": result["anzsic_codes"]}
        if self.ruleset.has_rule("sector_keyword_match") and result["keywords"]:
            existing = self.ruleset.rule("sector_keyword_match").logic
            existing["include"] = result["keywords"]
            self.ruleset.rule("sector_keyword_match").logic = existing
        return result

    def resolve_geography(self, states: list[str] | None = None, regions: list[str] | None = None) -> dict:
        result = resolvers.resolve_geography(states=states, regions=regions)
        if result["states"] and self.ruleset.has_rule("state"):
            logic = self.ruleset.rule("state").logic
            logic["values"] = result["states"]
            logic["postcodes"] = result["postcodes"]
            self.ruleset.rule("state").logic = logic
        self.postcodes = result["postcodes"]
        self.states = result["states"]
        return result

    def finalize_ruleset(self) -> dict:
        missing = self._missing_for_finalize()
        if missing:
            raise FinalizeError(f"cannot finalize, unresolved: {', '.join(missing)}")
        self.ruleset.confirmed = True
        if not self.ruleset.thesis_summary:
            self.ruleset.thesis_summary = self._build_thesis()
        return {"ok": True, "confirmed": True, "ruleset_id": self.ruleset.ruleset_id}

    # --- helpers ---
    def _missing_for_finalize(self) -> list[str]:
        missing: list[str] = []
        anzsic = self.ruleset.rule("anzsic_code").logic.get("values") if self.ruleset.has_rule("anzsic_code") else None
        keywords = self.ruleset.rule("sector_keyword_match").logic.get("include") if self.ruleset.has_rule("sector_keyword_match") else None
        if not anzsic:
            missing.append("sector_anzsic")
        if not keywords:
            missing.append("sector_keywords")
        if not self.postcodes:
            missing.append("geography_postcodes")
        return missing

    def _build_thesis(self) -> str:
        kws = self.ruleset.rule("sector_keyword_match").logic.get("include", []) if self.ruleset.has_rule("sector_keyword_match") else []
        states = self.states or (self.ruleset.rule("state").logic.get("values", []) if self.ruleset.has_rule("state") else [])
        sector = ", ".join(kws[:5]) or "target sector"
        geo = ", ".join(states) or "Australia"
        return (
            f"Founder-owned B2B {sector} businesses in {geo}. "
            "Screen on size, age and ownership at discovery; defer margins, customer "
            "concentration and seller motivation to the IM/call stage."
        )

    def dispatch(self, name: str, args: dict) -> dict:
        if name == "update_ruleset":
            return self.update_ruleset(args.get("field", ""), args.get("logic", {}))
        if name == "resolve_sector":
            return self.resolve_sector(args.get("intent_text", ""))
        if name == "resolve_geography":
            return self.resolve_geography(args.get("states"), args.get("regions"))
        if name == "finalize_ruleset":
            return self.finalize_ruleset()
        return {"ok": False, "error": f"unknown tool '{name}'"}
