"""RunManager — run lifecycle + the live buy-box agent sessions (plan §4.2/§4.3).

Owns three things:

1. **Sessions** — the ``buybox`` stage is a stateful multi-turn conversation; the
   manager parks each run's ``BuyBoxAgent`` in-process until the ruleset confirms
   (or the question cap flags ``needs_review``). A server restart strands these —
   documented limitation; the API answers 409.
2. **Launch** — on confirmation the ruleset is persisted (its id rewritten to
   ``rs_{run_id}`` so the base CSV id doesn't collide as the ``rulesets`` PK
   across runs; the original id survives in ``base_version``) and the pipeline is
   submitted to a ThreadPoolExecutor (default 1 worker — one run at a time, which
   serializes Apify + CPU-qwen contention).
3. **Reads** — thin passthroughs to the RunStore for the GET/select endpoints.

Everything is injectable: tests pass a scripted agent factory, a fake pipeline,
and an inline executor.
"""
from __future__ import annotations

import threading
import uuid
import warnings
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.run import Run, RunStatus

if TYPE_CHECKING:
    from ..agent.buybox_agent import AgentTurn, BuyBoxAgent
    from ..models.company import CompanyRecord
    from .pipeline import RunPipeline
    from .store import RunStore


class InlineExecutor(Executor):
    """Runs submitted work synchronously — for tests and the CLI path."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        from concurrent.futures import Future

        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirror executor semantics
            future.set_exception(exc)
        return future


@dataclass
class StartResult:
    run_id: str
    turn: AgentTurn


def _default_agent_factory() -> BuyBoxAgent:
    from ..agent.buybox_agent import BuyBoxAgent
    from ..config import get_settings
    from ..llm import get_llm_client
    from ..ruleset.loader import load_origo_ruleset

    s = get_settings()
    return BuyBoxAgent(
        llm=get_llm_client(s),
        base_ruleset=load_origo_ruleset(),
        model=s.agent_model,
        max_questions=s.max_clarifying_questions,
    )


class RunManager:
    def __init__(
        self,
        store: RunStore,
        *,
        pipeline: RunPipeline | None = None,
        agent_factory: Callable[[], BuyBoxAgent] | None = None,
        executor: Executor | None = None,
        settings: Any = None,
    ) -> None:
        from ..config import get_settings
        from .pipeline import RunPipeline

        s = settings or get_settings()
        self.store = store
        self._pipeline = pipeline or RunPipeline(store, settings=s)
        self._agent_factory = agent_factory or _default_agent_factory
        self._executor = executor or ThreadPoolExecutor(
            max_workers=s.run_workers, thread_name_prefix="run-pipeline"
        )
        self._sessions: dict[str, BuyBoxAgent] = {}
        self._demo_keys: dict[str, str] = {}  # run_id -> demo cache key (if the prompt matched)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Buy-box conversation
    # ------------------------------------------------------------------

    def start_run(self, message: str) -> StartResult:
        from . import demo_cache

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.store.create_run(run_id)

        # The first buy-box message is what we match against the demo cache — it is
        # deterministic, unlike the LLM-resolved ruleset the pipeline later runs on.
        key = demo_cache.match_prompt(message)
        if key:
            with self._lock:
                self._demo_keys[run_id] = key

        agent = self._agent_factory()
        turn = agent.step(message)

        # Persist the exchange so the chat survives a restart and can be re-opened.
        self.store.append_message(run_id, "user", message)
        self.store.append_message(run_id, "assistant", turn.text)

        if turn.ruleset.confirmed:
            self._launch(run_id, turn.ruleset)
        else:
            with self._lock:
                self._sessions[run_id] = agent
        return StartResult(run_id=run_id, turn=turn)

    def continue_buybox(self, run_id: str, message: str) -> AgentTurn:
        """Answer a clarifying question. Raises KeyError (unknown run) or
        LookupError (no live session — restart, or run already past buybox)."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != RunStatus.BUYBOX:
            raise LookupError(f"run {run_id} is past the buybox stage ({run.status.value})")

        with self._lock:
            agent = self._sessions.get(run_id)
        if agent is None:
            raise LookupError(f"no live buy-box session for {run_id} (server restarted?)")

        turn = agent.step(message)
        self.store.append_message(run_id, "user", message)
        self.store.append_message(run_id, "assistant", turn.text)
        if turn.ruleset.confirmed:
            with self._lock:
                self._sessions.pop(run_id, None)
            self._launch(run_id, turn.ruleset)
        return turn

    # ------------------------------------------------------------------
    # Pipeline launch
    # ------------------------------------------------------------------

    def _launch(self, run_id: str, ruleset) -> None:
        # The agent's copy keeps the base-CSV ruleset_id; rewriting it per run
        # avoids a rulesets-PK collision across runs (base id lives in base_version).
        ruleset = ruleset.model_copy(deep=True)
        ruleset.ruleset_id = f"rs_{run_id}"
        self.store.save_ruleset(ruleset)
        self.store.attach_ruleset(run_id, ruleset.ruleset_id)

        with self._lock:
            cache_key = self._demo_keys.pop(run_id, None)

        def _run() -> None:
            try:
                self._pipeline.execute(run_id, ruleset, cache_key=cache_key)
            except Exception as exc:  # noqa: BLE001
                # execute() already set FAILED; this guard catches store errors too.
                warnings.warn(f"run {run_id} failed: {exc}", stacklevel=2)

        self._executor.submit(_run)

    # ------------------------------------------------------------------
    # Reads (API passthroughs)
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> Run | None:
        return self.store.get_run(run_id)

    def get_company(self, run_id: str, abn: str) -> tuple[CompanyRecord, bool] | None:
        return self.store.get_company(run_id, abn)

    def select(self, run_id: str, abn: str) -> bool:
        return self.store.mark_selected(run_id, abn)

    def has_session(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._sessions

    # ------------------------------------------------------------------
    # Saved chats + conversational re-rank
    # ------------------------------------------------------------------

    def list_runs(self) -> list[dict]:
        return self.store.list_runs()

    def set_label(self, run_id: str, label: str) -> bool:
        if self.store.get_run(run_id) is None:
            return False
        self.store.set_label(run_id, label)
        return True

    def list_selected(self, run_id: str) -> list[CompanyRecord]:
        return self.store.list_selected(run_id)

    def query_shortlist(self, run_id: str, message: str) -> dict | None:
        """Conversational re-rank: NL → deterministic filter/sort over the shortlist.

        Returns ``{"spec": ..., "results": [...]}`` or ``None`` if the run/shortlist
        isn't available yet. Persists the exchange into the saved conversation.
        """
        from ..rank.pool_query import apply_query, parse_query

        run = self.store.get_run(run_id)
        if run is None or not run.shortlist:
            return None

        thesis = run.conversation[0]["text"] if run.conversation else ""
        spec = parse_query(message, thesis)
        results = apply_query(run.shortlist, spec)

        self.store.append_message(run_id, "user", message)
        self.store.append_message(
            run_id,
            "assistant",
            f"Re-ranked to {len(results)} of {len(run.shortlist)} companies.",
        )
        return {"spec": spec.model_dump(), "results": results}
