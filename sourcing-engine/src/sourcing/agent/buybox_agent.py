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

A `CURRENT RULESET STATE` block is appended to this prompt on EVERY turn. It is the
single source of truth for what is resolved, the current filter values, and what is
still required. Read it before every reply — never rely on memory of earlier turns.

On EVERY turn, structure your reply like this:
  1. **Narrate the ruleset back** in plain language as a short bulleted summary:
       - Sector: resolved (which ANZSIC codes + keywords) or not yet.
       - Geography: resolved (which states) or not yet.
       - Key filters the user set or that matter: EBITDA, years operating, ownership,
         size — state their current values.
       - **Still missing:** explicitly list every required field that is not yet
         resolved (copy them from the STILL REQUIRED line), and one clause on why each
         matters. If nothing is missing, say the ruleset is complete and ready.
  2. **Take the smallest action** that moves the ruleset forward, via a tool:
       - `resolve_sector`     — map the user's sector words to ANZSIC codes + keywords.
       - `resolve_geography`  — map states/regions to postcodes.
       - `update_ruleset`     — change ONE field the user explicitly specified.
       - `finalize_ruleset`   — ONLY once both sector AND geography are resolved.
  3. **Ask at most one** high-impact clarifying question, and only when the answer
     changes sourcing or ranking. If the user already gave a value, apply it with a
     tool immediately instead of re-asking.

Hard rules:
  - NEVER guess a field name. Use ONLY the exact names in the `EDITABLE FIELDS` list
    in the state block. If you are unsure which field the user means, name the
    candidate field(s) and ask — do not call `update_ruleset` on a guessed name.
  - Always call `resolve_sector` and `resolve_geography` before `finalize_ruleset`.
  - The base ruleset already has sensible defaults — do not re-ask a field that is
    already set unless the user wants to change it.
  - If a tool fails (e.g. an unknown field), tell the user plainly what went wrong
    and exactly what you need to proceed.
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

    def _state_block(self) -> str:
        """Render the live ruleset snapshot appended to the system prompt each turn.

        Gives the model exact field names (so it stops guessing) and an explicit
        resolved/missing view (so it can narrate the ruleset every turn).
        """
        st = self.editor.resolution_state()
        lines = ["", "=== CURRENT RULESET STATE (refreshed every turn) ==="]

        if st["sector_resolved"]:
            lines.append(
                f"Sector: RESOLVED — ANZSIC {', '.join(st['anzsic_codes'])}; "
                f"keywords {', '.join(st['sector_keywords'])}"
            )
        else:
            lines.append("Sector: NOT RESOLVED — call resolve_sector")

        if st["geography_resolved"]:
            lines.append(f"Geography: RESOLVED — {', '.join(st['states']) or 'postcodes set'}")
        else:
            lines.append("Geography: NOT RESOLVED — call resolve_geography")

        if st["settings"]:
            lines.append("Current filter values:")
            for f, logic in st["settings"].items():
                lines.append(f"  - {f}: {logic}")

        if st["missing"]:
            lines.append("STILL REQUIRED before finalize_ruleset:")
            for m in st["missing"]:
                lines.append(f"  - {m}")
        else:
            lines.append("All required fields resolved — you may call finalize_ruleset.")

        lines.append("")
        lines.append("=== EDITABLE FIELDS (use these EXACT names in update_ruleset) ===")
        for group, names in self.editor.fields_by_group().items():
            lines.append(f"  {group}: {', '.join(names)}")

        return "\n".join(lines)

    def step(self, user_msg: str) -> AgentTurn:
        self.history.append({"role": "user", "content": user_msg})
        resp = self.llm.chat(
            model=self.model,
            system=SYSTEM_PROMPT + "\n" + self._state_block(),
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
