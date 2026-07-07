"""IPGODConnector — IP Australia open data as an ABN-keyed moat signal (plan §3).

IPGOD (Intellectual Property Government Open Data) publishes applicant-level
CSVs per IP right (patents, trade marks, designs, plant breeder's rights) on
data.gov.au, refreshed annually. Applicant rows carry the applicant's ABN, which
makes this a direct, authoritative join onto the spine — no name resolution.

The table is the *aggregate*, not the raw rows: one row per (abn, ip_type) with
``ip_count`` and ``earliest_year``. That is all the judge and the moat signal
need, and it keeps the DuckDB footprint tiny regardless of source CSV size.

Column drift note: IPGOD releases vary column names across years and IP rights
(``abn`` vs ``applicant_abn``; ``application_year`` vs a filing-date column).
The loader introspects each CSV via DESCRIBE and picks columns defensively:
first column containing "abn", first containing "year", else the year is
extracted from the first column containing "date".

``normalize()`` produces a *fragment* record (abn + moat_signals); the usual
consumer is ``enrich_record(record)``, which merges IP counts onto an existing
``CompanyRecord`` in the enrichment waterfall.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import get_settings
from .base_bulk import BulkConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "ipgod"

# Direct ABN join on an authoritative register, but the data is annually stale.
CONFIDENCE = 0.9

# Filename fragment → ip_type. Checked in order; first hit wins.
_IP_TYPE_HINTS: list[tuple[str, str]] = [
    ("patent", "patent"),
    ("trade", "trademark"),
    ("design", "design"),
    ("plant", "plant_breeder"),
]


def _infer_ip_type(path: str) -> str:
    name = Path(path).name.lower()
    for fragment, ip_type in _IP_TYPE_HINTS:
        if fragment in name:
            return ip_type
    return "unknown"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class IPGODConnector(BulkConnector):
    source_id: str = SOURCE_ID
    table_name: str = "ipgod"

    def __init__(
        self,
        db_path: Path | None = None,
        csv_paths: list[str] | dict[str, str] | None = None,
    ) -> None:
        """``csv_paths``: list (ip_type inferred per filename) or {path: ip_type}."""
        super().__init__(db_path=db_path)
        if csv_paths is None:
            raw = get_settings().ipgod_csv_paths
            csv_paths = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        if isinstance(csv_paths, dict):
            self._sources: list[tuple[str, str]] = list(csv_paths.items())
        else:
            self._sources = [(p, _infer_ip_type(p)) for p in csv_paths]

    @classmethod
    def from_settings(cls) -> IPGODConnector:
        return cls()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def download(self) -> None:
        """Manual-download source: validate the configured CSVs are present.

        Production CKAN-resolves the IPGOD package (annual release); local dev
        points IPGOD_CSV_PATHS at downloaded applicant CSVs.
        """
        if not self._sources:
            raise FileNotFoundError(
                "No IPGOD CSVs configured. Set IPGOD_CSV_PATHS in .env to one or "
                "more applicant-level CSVs from the data.gov.au IPGOD dataset."
            )
        missing = [p for p, _ in self._sources if not Path(p).exists()]
        if missing:
            raise FileNotFoundError(f"IPGOD CSV(s) not found: {missing}")

    def load(self) -> None:
        """Aggregate every applicant CSV into one (abn, ip_type) row with counts."""
        self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")
        self.conn.execute(
            f"""
            CREATE TABLE {self.table_name} (
                abn VARCHAR, ip_type VARCHAR, ip_count BIGINT, earliest_year INTEGER
            )
            """
        )
        for path, ip_type in self._sources:
            csv = path.replace("'", "''")
            self.conn.execute("DROP VIEW IF EXISTS _ipgod_src")
            self.conn.execute(
                f"""
                CREATE TEMP VIEW _ipgod_src AS
                SELECT * FROM read_csv(
                    '{csv}',
                    header=true, all_varchar=true,
                    normalize_names=true, sample_size=-1, ignore_errors=true
                )
                """
            )
            cols = [r["column_name"] for r in self.query("DESCRIBE _ipgod_src")]

            def find(fragment: str, cols: list[str] = cols) -> str | None:
                for c in cols:
                    if fragment in c.lower():
                        return c
                return None

            abn_col = find("abn")
            if abn_col is None:
                raise ValueError(
                    f"IPGOD CSV {path!r} has no ABN column (columns: {cols})."
                )
            # IPGOD2022 exports ABNs float-formatted ("35626671467.0") — strip a
            # trailing ".0*" before digit-cleaning or every ABN becomes 12 digits.
            clean_abn = (
                f"regexp_replace(regexp_replace(trim({abn_col}), '\\.[0-9]*$', ''), "
                "'[^0-9]', '', 'g')"
            )
            year_col = find("year")
            if year_col is not None:
                year_expr = f"try_cast({year_col} AS INTEGER)"
            else:
                date_col = find("date")
                # Year prefix of an ISO-ish date string; NULL when absent.
                year_expr = (
                    f"try_cast(substr(trim({date_col}), 1, 4) AS INTEGER)"
                    if date_col
                    else "NULL"
                )
            # Party-activity files (IPGOD2022) repeat one application across many
            # party rows and roles: count distinct applications, applicants only —
            # agents (patent attorneys) and mortgagees are the wrong signal.
            app_col = find("application_number") or find("appl_no") or find("appl")
            count_expr = f"count(DISTINCT {app_col})" if app_col else "count(*)"
            role_col = find("role_category")
            role_clause = f"AND {role_col} = 'applicant'" if role_col else ""

            ip_type_sql = ip_type.replace("'", "''")
            self.conn.execute(
                f"""
                INSERT INTO {self.table_name}
                SELECT
                    {clean_abn} AS abn,
                    '{ip_type_sql}' AS ip_type,
                    {count_expr} AS ip_count,
                    min({year_expr}) AS earliest_year
                FROM _ipgod_src
                WHERE {abn_col} IS NOT NULL
                  AND length({clean_abn}) = 11
                  {role_clause}
                GROUP BY 1
                """
            )
        # Two CSVs may share an ip_type (e.g. two patent releases) — re-aggregate
        # so (abn, ip_type) stays unique.
        self.conn.execute(
            f"""
            CREATE OR REPLACE TABLE {self.table_name} AS
            SELECT abn, ip_type, sum(ip_count) AS ip_count,
                   min(earliest_year) AS earliest_year
            FROM {self.table_name}
            GROUP BY abn, ip_type
            """
        )
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_abn ON {self.table_name}(abn)"
        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """One RawRecord per (abn, ip_type) row for the given ABN.

        Supported params:
            abn:   the ABN to look up (required for a useful result)
            limit: row cap when no abn given (default 1000)
        """
        self.ensure_loaded()
        abn = params.get("abn")
        if abn:
            rows = self.query(
                f"SELECT * FROM {self.table_name} WHERE abn = ? ORDER BY ip_type",
                [_digits(abn)],
            )
        else:
            limit = int(params.get("limit", 1000))
            rows = self.query(f"SELECT * FROM {self.table_name} LIMIT {limit}")
        return [self._row_to_raw(r) for r in rows]

    def has_ip(self, abn: str) -> bool:
        """Existence check on the aggregated table."""
        self.ensure_loaded()
        rows = self.query(
            f"SELECT 1 FROM {self.table_name} WHERE abn = ? LIMIT 1", [_digits(abn)]
        )
        return bool(rows)

    # ------------------------------------------------------------------
    # Normalise / enrich
    # ------------------------------------------------------------------

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import CompanyRecord, MoatSignals, Provenance

        detail = raw.get("raw") or {}
        ip_type = detail.get("ip_type", "unknown")
        ip_count = int(detail.get("ip_count") or 0)
        earliest = detail.get("earliest_year")
        abn = raw.get("abn")

        return CompanyRecord(
            entity_id=f"abn:{abn}",
            abn=abn,
            moat_signals=MoatSignals(ip=True, ip_count=ip_count, ip_types=[ip_type]),
            provenance=[
                Provenance(
                    field="moat_signals.ip",
                    source=SOURCE_ID,
                    locator=f"ip_type={ip_type}; earliest={earliest}",
                    fetched_at=raw.get("fetched_at", ""),
                    confidence=CONFIDENCE,
                )
            ],
        )

    def enrich_record(self, record: CompanyRecord) -> CompanyRecord:
        """Merge IP counts onto an existing record; leave ``ip`` None on a miss.

        IPGOD's ABN coverage is partial and annually stale, so absence is weak
        evidence — the miss is recorded as the ``ipgod_checked_no_ip`` flag, not
        ``ip = False``.
        """
        from ..models.company import Provenance

        if not record.abn:
            return record

        rows = self.fetch({"abn": record.abn})
        if not rows:
            record.flags.append("ipgod_checked_no_ip")
            return record

        details = [r.get("raw") or {} for r in rows]
        record.moat_signals.ip = True
        record.moat_signals.ip_count = sum(int(d.get("ip_count") or 0) for d in details)
        record.moat_signals.ip_types = sorted({d.get("ip_type", "unknown") for d in details})
        record.provenance.append(
            Provenance(
                field="moat_signals.ip",
                source=SOURCE_ID,
                locator=f"abn={_digits(record.abn)}; {len(rows)} ip_type rows",
                fetched_at=_now_z(),
                confidence=CONFIDENCE,
            )
        )
        return record

    # ------------------------------------------------------------------
    # Row → RawRecord mapping
    # ------------------------------------------------------------------

    def _row_to_raw(self, row: dict[str, Any]) -> RawRecord:
        return RawRecord(
            source_id=SOURCE_ID,
            abn=row.get("abn"),
            raw=dict(row),
        )
