"""BulkConnector — base class for download-once, query-locally sources (plan §2.2).

A bulk source (ASIC companies, ABN bulk, IPGOD, ASX) is downloaded once, parsed,
and loaded into a shared DuckDB database (``data/bulk.duckdb``). After that,
``fetch()`` answers from local SQL — fast, offline, and free.

Lifecycle (``ensure_loaded`` orchestrates):
    download()  → obtain the raw file(s) on disk (no-op if already local)
    parse()     → optional cleaning step (most subclasses load directly)
    load()      → create + populate the DuckDB table, build indexes
    query(sql)  → run a read query against the table

Subclasses set ``source_id`` and ``table_name`` and implement ``download``,
``load``, and ``normalize`` (plus point-lookup helpers like ``lookup_acn``).

DuckDB is used instead of pandas because the real datasets are large (the ASIC
extract is ~4.4M rows); DuckDB reads CSV natively, preserves leading zeros with
``all_varchar``, and indexes/joins in-process without a server.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR
from .protocol import RawRecord

if TYPE_CHECKING:
    import duckdb

    from ..models.company import CompanyRecord

# Single shared DuckDB file for all bulk sources, so cross-source joins
# (e.g. abn ⋈ asic on acn) are one SQL statement.
BULK_DB_PATH = DATA_DIR / "bulk.duckdb"

# Fix 7: per-db-path locks prevent concurrent ensure_loaded() TOCTOU races.
# DuckDB supports one writer; two threads that both pass the table_exists() check
# would both call load() → DROP TABLE + CREATE TABLE → one destroys the other's data.
_db_locks: dict[str, threading.Lock] = {}
_db_locks_meta = threading.Lock()


def _get_db_lock(db_path: str) -> threading.Lock:
    with _db_locks_meta:
        if db_path not in _db_locks:
            _db_locks[db_path] = threading.Lock()
        return _db_locks[db_path]


class BulkConnector:
    """Base for bulk-file connectors backed by a local DuckDB table.

    Class attributes (override in subclass):
        source_id:    registry id, e.g. ``"asic_companies"``
        table_name:   DuckDB table to create/query, e.g. ``"asic_companies"``
    """

    source_id: str = ""
    table_name: str = ""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or BULK_DB_PATH
        self._conn: duckdb.DuckDBPyConnection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            import duckdb

            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self._db_path))
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Load lifecycle
    # ------------------------------------------------------------------

    def table_exists(self) -> bool:
        rows = self.conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [self.table_name],
        ).fetchone()
        return bool(rows and rows[0] > 0)

    def row_count(self) -> int:
        if not self.table_exists():
            return 0
        rows = self.conn.execute(f"SELECT count(*) FROM {self.table_name}").fetchone()
        return int(rows[0]) if rows else 0

    def ensure_loaded(self, *, force: bool = False) -> int:
        """Make sure the table exists and is populated; return its row count.

        Idempotent: if the table is already loaded (and not ``force``), this is
        a cheap row-count check with no re-download.

        Fix 7: the check-then-act sequence is guarded by a per-db-path lock so
        two threads cannot both pass ``table_exists() == False`` and both call
        ``load()`` (which drops and re-creates the table, corrupting each other).
        """
        lock = _get_db_lock(str(self._db_path))
        with lock:
            if force and self.table_exists():
                self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")
            if not self.table_exists() or self.row_count() == 0:
                self.download()
                self.parse()
                self.load()
        return self.row_count()

    # ------------------------------------------------------------------
    # Steps — subclasses override download/load (parse is optional)
    # ------------------------------------------------------------------

    def download(self) -> None:  # pragma: no cover - subclass responsibility
        """Obtain the raw source file(s) on disk. No-op when already local."""

    def parse(self) -> None:  # pragma: no cover - optional
        """Optional cleaning step between download and load."""

    def load(self) -> None:  # pragma: no cover - subclass responsibility
        """Create and populate ``table_name`` in DuckDB, then build indexes."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def query(self, sql: str, params: list | None = None) -> list[dict[str, Any]]:
        """Run a read query and return a list of column→value dicts."""
        cur = self.conn.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Contract — subclasses implement these
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:  # pragma: no cover - abstract
        raise NotImplementedError

    def normalize(self, raw: RawRecord) -> CompanyRecord:  # pragma: no cover - abstract
        raise NotImplementedError
