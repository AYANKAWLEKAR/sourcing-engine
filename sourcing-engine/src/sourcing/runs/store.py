"""RunStore — persistence seam for runs (next-phase plan §4.1).

Dual-impl pattern (like ``connectors/cache.py`` and ``rag/vector_store.py``):

* ``InMemoryRunStore`` — lock-guarded dicts; unit tests and ``--no-db`` demos.
* ``PostgresRunStore`` — one ``session_scope()`` per method; writes the
  ``runs`` / ``run_companies`` / ``rulesets`` / ``filter_rules`` / ``companies``
  tables (the first production writes to all of them).

Both the pipeline thread and the API threadpool touch the store concurrently,
so every implementation must be thread-safe.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from ..models.run import Run, RunStatus

if TYPE_CHECKING:
    from ..models.company import CompanyRecord
    from ..models.filter_rule import FilterRuleset
    from ..models.ranking import RankedCompany
    from ..models.source import SourcePlanItem


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# Shortlist rows exclude the bulky raw page text — the full record (including
# website_text_raw) is persisted separately in the companies table.
_SHORTLIST_EXCLUDE = {"record": {"website_text_raw"}}


def shortlist_dump(shortlist: list[RankedCompany]) -> list[dict]:
    return [rc.model_dump(exclude=_SHORTLIST_EXCLUDE) for rc in shortlist]


class RunStore(Protocol):
    def create_run(self, run_id: str) -> None: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None: ...
    def save_ruleset(self, ruleset: FilterRuleset) -> None: ...
    def attach_ruleset(self, run_id: str, ruleset_id: str) -> None: ...
    def save_source_plan(self, run_id: str, plan: list[SourcePlanItem]) -> None: ...
    def update_coverage(self, run_id: str, **counters: Any) -> None: ...
    def save_company(self, run_id: str, record: CompanyRecord) -> None: ...
    def get_company(self, run_id: str, abn: str) -> tuple[CompanyRecord, bool] | None: ...
    def save_shortlist(self, run_id: str, shortlist: list[RankedCompany]) -> None: ...
    def mark_selected(self, run_id: str, abn: str) -> bool: ...
    def list_runs(self) -> list[dict]: ...
    def append_message(self, run_id: str, role: str, text: str) -> None: ...
    def set_label(self, run_id: str, label: str) -> None: ...
    def list_selected(self, run_id: str) -> list[CompanyRecord]: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryRunStore:
    """Thread-safe, process-local store for unit tests and --no-db demos."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._rulesets: dict[str, Any] = {}
        # (run_id, abn) -> [record_dump, selected]
        self._companies: dict[tuple[str, str], list] = {}

    def create_run(self, run_id: str) -> None:
        with self._lock:
            now = _now_iso()
            self._runs[run_id] = Run(
                run_id=run_id,
                status=RunStatus.BUYBOX,
                stage_history=[{"status": RunStatus.BUYBOX.value, "at": now}],
                created_at=now,
                updated_at=now,
            )

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        with self._lock:
            run = self._runs[run_id]
            now = _now_iso()
            run.status = status
            run.updated_at = now
            run.stage_history.append({"status": status.value, "at": now})
            if error is not None:
                run.error = error

    def save_ruleset(self, ruleset: FilterRuleset) -> None:
        with self._lock:
            self._rulesets[ruleset.ruleset_id] = ruleset.model_copy(deep=True)

    def attach_ruleset(self, run_id: str, ruleset_id: str) -> None:
        with self._lock:
            self._runs[run_id].ruleset_id = ruleset_id

    def save_source_plan(self, run_id: str, plan: list[SourcePlanItem]) -> None:
        with self._lock:
            self._runs[run_id].source_plan = [p.model_copy(deep=True) for p in plan]

    def update_coverage(self, run_id: str, **counters: Any) -> None:
        with self._lock:
            self._runs[run_id].coverage.update(counters)

    def save_company(self, run_id: str, record: CompanyRecord) -> None:
        if not record.abn:
            return
        with self._lock:
            key = (run_id, record.abn)
            selected = self._companies.get(key, [None, False])[1]
            self._companies[key] = [record.model_dump(), selected]

    def get_company(self, run_id: str, abn: str) -> tuple[CompanyRecord, bool] | None:
        from ..models.company import CompanyRecord

        with self._lock:
            entry = self._companies.get((run_id, abn))
            if entry is None:
                return None
            return CompanyRecord(**entry[0]), entry[1]

    def save_shortlist(self, run_id: str, shortlist: list[RankedCompany]) -> None:
        dumped = shortlist_dump(shortlist)
        with self._lock:
            self._runs[run_id].shortlist = dumped

    def mark_selected(self, run_id: str, abn: str) -> bool:
        with self._lock:
            entry = self._companies.get((run_id, abn))
            if entry is None:
                return False
            entry[1] = True
            return True

    def list_runs(self) -> list[dict]:
        with self._lock:
            runs = sorted(
                self._runs.values(),
                key=lambda r: r.created_at or "",
                reverse=True,
            )
            return [_run_summary(r) for r in runs]

    def append_message(self, run_id: str, role: str, text: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.conversation.append({"role": role, "text": text, "at": _now_iso()})

    def set_label(self, run_id: str, label: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.label = label

    def list_selected(self, run_id: str) -> list[CompanyRecord]:
        from ..models.company import CompanyRecord

        with self._lock:
            out = []
            for (rid, _abn), entry in self._companies.items():
                if rid == run_id and entry[1]:
                    out.append(CompanyRecord(**entry[0]))
            return out


def _run_summary(run: Run) -> dict:
    """Compact listing row for the saved-runs sidebar."""
    return {
        "run_id": run.run_id,
        "label": run.label,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "thesis": (run.conversation[0]["text"] if run.conversation else None),
        "n_shortlist": len(run.shortlist) if run.shortlist else 0,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

class PostgresRunStore:
    """Persists runs to Postgres. One short session per method (thread-safe)."""

    def create_run(self, run_id: str) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        now = _now_iso()
        with session_scope() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    status=RunStatus.BUYBOX.value,
                    stage_history=[{"status": RunStatus.BUYBOX.value, "at": now}],
                    source_plan=[],
                    coverage={},
                )
            )

    def get_run(self, run_id: str) -> Run | None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            return Run(
                run_id=row.run_id,
                status=RunStatus(row.status),
                error=row.error,
                ruleset_id=row.ruleset_id,
                label=row.label,
                source_plan=row.source_plan or [],
                coverage=row.coverage or {},
                shortlist=row.shortlist,
                conversation=row.conversation or [],
                stage_history=row.stage_history or [],
                created_at=row.created_at.isoformat() if row.created_at else None,
                updated_at=row.updated_at.isoformat() if row.updated_at else None,
            )

    def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.status = status.value
            # JSON columns need reassignment (in-place append is not tracked).
            row.stage_history = [
                *(row.stage_history or []),
                {"status": status.value, "at": _now_iso()},
            ]
            if error is not None:
                row.error = error

    def save_ruleset(self, ruleset: FilterRuleset) -> None:
        from ..db import session_scope
        from ..tables.core import FilterRuleRow, RulesetRow

        with session_scope() as session:
            # Delete-then-insert keeps re-saves idempotent.
            existing = session.get(RulesetRow, ruleset.ruleset_id)
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(
                RulesetRow(
                    ruleset_id=ruleset.ruleset_id,
                    name=ruleset.name,
                    base_version=ruleset.base_version,
                    thesis_summary=ruleset.thesis_summary,
                    ranking_weights=ruleset.ranking_weights or {},
                    created_by=ruleset.created_by,
                    confirmed=ruleset.confirmed,
                )
            )
            # No ORM relationship() links these tables, so the unit of work can't
            # order the inserts — flush the parent before the FK'd rule rows.
            session.flush()
            for rule in ruleset.rules:
                session.add(
                    FilterRuleRow(
                        ruleset_id=ruleset.ruleset_id,
                        field=rule.field,
                        group=rule.group,
                        data_type=rule.data_type,
                        filter_type=rule.filter_type,
                        screen_tier=rule.screen_tier.value,
                        logic=rule.logic or {},
                        sources=rule.sources or [],
                        scrapeable=rule.scrapeable,
                        proxyable=rule.proxyable,
                        discovery_action=rule.discovery_action.value,
                        weight=rule.weight,
                        notes=rule.notes,
                    )
                )

    def attach_ruleset(self, run_id: str, ruleset_id: str) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.ruleset_id = ruleset_id

    def save_source_plan(self, run_id: str, plan: list[SourcePlanItem]) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.source_plan = [p.model_dump() for p in plan]

    def update_coverage(self, run_id: str, **counters: Any) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.coverage = {**(row.coverage or {}), **counters}

    def save_company(self, run_id: str, record: CompanyRecord) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from ..connectors.ingest import upsert_companies
        from ..db import session_scope
        from ..tables.core import RunCompanyRow

        if not record.abn:
            return
        with session_scope() as session:
            # Full record into companies (existing upsert; commits internally —
            # a second harmless commit comes from session_scope).
            upsert_companies(session, [record])
            stmt = (
                pg_insert(RunCompanyRow)
                .values(run_id=run_id, entity_id=record.entity_id, abn=record.abn)
                .on_conflict_do_nothing(index_elements=["run_id", "entity_id"])
            )
            session.execute(stmt)

    def get_company(self, run_id: str, abn: str) -> tuple[CompanyRecord, bool] | None:
        import json

        from sqlalchemy import select, text

        from ..db import session_scope
        from ..models.company import CompanyRecord
        from ..tables.core import RunCompanyRow

        with session_scope() as session:
            link = session.execute(
                select(RunCompanyRow).where(
                    RunCompanyRow.run_id == run_id, RunCompanyRow.abn == abn
                )
            ).scalar_one_or_none()
            if link is None:
                return None
            row = session.execute(
                text("SELECT record FROM companies WHERE entity_id = :eid"),
                {"eid": link.entity_id},
            ).fetchone()
            if row is None or row[0] is None:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return CompanyRecord(**payload), link.selected

    def save_shortlist(self, run_id: str, shortlist: list[RankedCompany]) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        dumped = shortlist_dump(shortlist)
        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            row.shortlist = dumped

    def mark_selected(self, run_id: str, abn: str) -> bool:
        from sqlalchemy import select

        from ..db import session_scope
        from ..tables.core import RunCompanyRow

        with session_scope() as session:
            link = session.execute(
                select(RunCompanyRow).where(
                    RunCompanyRow.run_id == run_id, RunCompanyRow.abn == abn
                )
            ).scalar_one_or_none()
            if link is None:
                return False
            link.selected = True
            return True

    def list_runs(self) -> list[dict]:
        from sqlalchemy import select

        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            rows = session.execute(
                select(RunRow).order_by(RunRow.created_at.desc())
            ).scalars().all()
            return [
                {
                    "run_id": r.run_id,
                    "label": r.label,
                    "status": r.status,
                    "thesis": (r.conversation[0]["text"] if r.conversation else None),
                    "n_shortlist": len(r.shortlist) if r.shortlist else 0,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]

    def append_message(self, run_id: str, role: str, text: str) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return
            row.conversation = [
                *(row.conversation or []),
                {"role": role, "text": text, "at": _now_iso()},
            ]

    def set_label(self, run_id: str, label: str) -> None:
        from ..db import session_scope
        from ..tables.core import RunRow

        with session_scope() as session:
            row = session.get(RunRow, run_id)
            if row is not None:
                row.label = label

    def list_selected(self, run_id: str) -> list[CompanyRecord]:
        import json

        from sqlalchemy import select, text

        from ..db import session_scope
        from ..models.company import CompanyRecord
        from ..tables.core import RunCompanyRow

        with session_scope() as session:
            links = session.execute(
                select(RunCompanyRow).where(
                    RunCompanyRow.run_id == run_id, RunCompanyRow.selected.is_(True)
                )
            ).scalars().all()
            out: list[CompanyRecord] = []
            for link in links:
                row = session.execute(
                    text("SELECT record FROM companies WHERE entity_id = :eid"),
                    {"eid": link.entity_id},
                ).fetchone()
                if row and row[0] is not None:
                    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    out.append(CompanyRecord(**payload))
            return out
