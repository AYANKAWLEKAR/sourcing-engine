"""Buy-Box Agent — bounded multi-turn tool-use loop (plan §6).

Loads the base ruleset (defaults already populated) and runs a capped multi-turn
conversation, confirming/overriding discovery-relevant fields and resolving sector
and geography via tools, until it emits a confirmed :class:`FilterRuleset` or hits
the clarifying-question cap.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..llm import LLMClient
from ..models.filter_rule import FilterRuleset
from .tools import TOOL_SCHEMAS, FinalizeError, RulesetEditor

SYSTEM_PROMPT = """\
You are the Origo Buy-Box Agent. You turn a searcher's natural-language buy box
into a confirmed acquisition Filter Ruleset.

The base ruleset already has sensible defaults for every field. Your job is NOT to
re-ask everything — it is to:
  1. Resolve the SECTOR from the user's description by calling `resolve_sector`.
  2. Resolve the GEOGRAPHY (states/regions) by calling `resolve_geography`.
  3. Override any size/age/ownership default the user explicitly changes, via `update_ruleset`.
  4. Ask at most a few high-impact clarifying questions, one at a time, only when a
     field is ambiguous AND it changes sourcing or ranking.
  5. When sector and geography are resolved, call `finalize_ruleset`.

Always call `resolve_sector` and `resolve_geography` before `finalize_ruleset`.
Keep replies short. Do not invent fields.
"""


@dataclass
class AgentTurn:
    text: str
    ruleset: FilterRuleset
    done: bool
    needs_review: bool = False
    tool_results: list[dict] = field(default_factory=list)


class BuyBoxAgent:
    def __init__(
        self,
        llm: LLMClient,
        base_ruleset: FilterRuleset,
        model: str,
        max_questions: int = 6,
    ):
        self.llm = llm
        self.model = model
        self.max_questions = max_questions
        self.questions_asked = 0
        self.history: list[dict] = []
        self.editor = RulesetEditor(
            base_ruleset.model_copy(deep=True), llm=llm, model=model
        )

    @property
    def state(self) -> FilterRuleset:
        return self.editor.ruleset

    def step(self, user_msg: str) -> AgentTurn:
        self.history.append({"role": "user", "content": user_msg})
        resp = self.llm.chat(
            model=self.model,
            system=SYSTEM_PROMPT,
            messages=self.history,
            tools=TOOL_SCHEMAS,
        )

        # Record the assistant turn (with any tool calls) for conversational context.
        assistant_msg: dict = {"role": "assistant", "content": resp.text}
        if resp.tool_calls:
            assistant_msg["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in resp.tool_calls
            ]
        self.history.append(assistant_msg)

        tool_results: list[dict] = []
        for tc in resp.tool_calls:
            try:
                result = self.editor.dispatch(tc.name, tc.arguments)
            except FinalizeError as exc:
                result = {"ok": False, "error": str(exc)}
            tool_results.append({"tool": tc.name, "result": result})
            self.history.append(
                {"role": "tool", "tool_name": tc.name, "content": json.dumps(result)}
            )

        if resp.asked_question:
            self.questions_asked += 1

        cap_hit = self.questions_asked >= self.max_questions
        done = self.state.confirmed or cap_hit
        needs_review = cap_hit and not self.state.confirmed
        return AgentTurn(
            text=resp.text,
            ruleset=self.state,
            done=done,
            needs_review=needs_review,
            tool_results=tool_results,
        )
