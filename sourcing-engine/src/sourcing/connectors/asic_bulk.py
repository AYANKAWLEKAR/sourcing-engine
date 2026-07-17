"""ASICBulkConnector — ASIC Company Dataset as a DuckDB-backed spine (plan §3.2).

The ASIC "Companies" dataset is the registry of every Australian company keyed on
ACN. The local extract is a large (~4.4M row) tab-delimited, UTF-8-with-BOM file
whose columns include both ``ACN`` and ``ABN`` — so this one source provides the
ACN→ABN bridge the resolver needs, plus company status, type, and registration
date for age filters.

Loading notes (why DuckDB, not pandas):
  * ``all_varchar=true`` preserves ACN/ABN leading zeros (integer inference would
    strip them — the plan's "dtype=str" intent).
  * ``normalize_names=true`` gives clean snake_case columns and strips the BOM.
  * dates are ``DD/MM/YYYY``; ``try_strptime`` parses them and yields NULL on blanks.
  * 4.4M rows load in seconds and index in-process — no server, no memory blow-up.

Methods:
  * ``lookup_acn(acn)`` / ``lookup_abn(abn)`` — indexed point lookups.
  * ``fetch({entity_types, min_years, status, state, limit})`` — candidate slice.
  * ``normalize(raw)`` — ACN/ABN/name/status/entity_type/asic_registered + provenance.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import get_settings
from .base_bulk import BulkConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

# Registry id (matches data/source_registry.yaml); the DuckDB table is named
# separately (``asic_companies``) below.
SOURCE_ID = "asic_company_dataset"

# ASIC "Type" codes → ownership structure guess. (Class/Sub Class refine further;
# we keep the common, high-signal mappings and leave the rest unguessed.)
_TYPE_STRUCTURE: dict[str, str] = {
    "APTY": "private-company",   # Australian proprietary company
    "APUB": "public-company",    # Australian public company
    "BENF": "private-company",   # Building/financial — treat as company
    "NONC": "association",       # Non-company (registered body)
    "REGS": "government",        # Registered scheme / statutory
}

# Active-registration status code.
_ACTIVE_STATUS = "REGD"


class ASICBulkConnector(BulkConnector):
    source_id: str = SOURCE_ID
    table_name: str = "asic_companies"

    def __init__(self, db_path: Path | None = None, csv_path: str | None = None) -> None:
        super().__init__(db_path=db_path)
        self._csv_path = csv_path or get_settings().asic_csv_path

    @classmethod
    def from_settings(cls) -> ASICBulkConnector:
        return cls(csv_path=get_settings().asic_csv_path)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def download(self) -> None:
        """The dev path uses a local CSV. Production CKAN-resolves from data.gov.au.

        We only validate the file is present here; CKAN resolution is a later
        production concern (plan §3.2 / §10).
        """
        if not self._csv_path or not Path(self._csv_path).exists():
            raise FileNotFoundError(
                "ASIC CSV not found. Set ASIC_CSV_PATH in .env to a downloaded copy "
                f"(got: {self._csv_path!r})."
            )

    def load(self) -> None:
        """Create the typed ``asic_companies`` table from the CSV and index it."""
        csv = self._csv_path.replace("'", "''")  # escape single quotes for SQL literal
        self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")

        # Probe the CSV headers using a cheap metadata query
        desc_rows = self.conn.execute(
            f"SELECT * FROM read_csv('{csv}', delim='\\t', header=true, all_varchar=true, normalize_names=true, sample_size=-1, ignore_errors=true) LIMIT 0"
        ).description
        cols = [r[0] for r in desc_rows]

        # Map actual columns dynamically to support header corruptions/renamings
        company_name_col = next((c for c in cols if c == "company_name" or c.endswith("company_name")), "company_name")
        acn_col = next((c for c in cols if c == "acn"), "acn")
        abn_col = next((c for c in cols if c == "abn"), "abn")
        current_name_col = next((c for c in cols if c == "current_name"), "current_name")
        indicator_col = next((c for c in cols if c == "current_name_indicator" or c.endswith("name_indicator") or c.endswith("indicator")), "current_name_indicator")
        type_col = next((c for c in cols if c == "_type" or c == "type"), "_type")
        class_col = next((c for c in cols if c == "_class" or c == "class"), "_class")
        sub_class_col = next((c for c in cols if c == "sub_class"), "sub_class")
        status_col = next((c for c in cols if c == "status"), "status")
        reg_date_col = next((c for c in cols if c == "date_of_registration" or c.endswith("registration")), "date_of_registration")
        dereg_date_col = next((c for c in cols if c == "date_of_deregistration" or c.endswith("deregistration")), "date_of_deregistration")
        state_col = next((c for c in cols if c == "previous_state_of_registration" or c == "previous_state"), "previous_state_of_registration")

        self.conn.execute(
            f"""
            CREATE TABLE {self.table_name} AS
            SELECT
                {company_name_col}                               AS company_name,
                {acn_col}                                        AS acn,
                {abn_col}                                        AS abn,
                {current_name_col}                               AS current_name,
                {indicator_col}                                  AS name_indicator,
                {type_col}                                       AS type,
                {class_col}                                      AS class,
                {sub_class_col}                                  AS sub_class,
                {status_col}                                     AS status,
                try_strptime({reg_date_col},   '%d/%m/%Y')::DATE AS registration_date,
                try_strptime({dereg_date_col}, '%d/%m/%Y')::DATE AS deregistration_date,
                {state_col}                                      AS previous_state
            FROM read_csv(
                '{csv}',
                delim='\t', header=true, all_varchar=true,
                normalize_names=true, sample_size=-1, ignore_errors=true
            )
            """
        )
        # Indexes: ACN for the join/point lookup, ABN for the resolver bridge, status for fetch.
        self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_acn ON {self.table_name}(acn)")
        self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_abn ON {self.table_name}(abn)")
        self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_status ON {self.table_name}(status)")

    # ------------------------------------------------------------------
    # Point lookups
    # ------------------------------------------------------------------

    def lookup_acn(self, acn: str) -> RawRecord | None:
        """Return the canonical record for an ACN (the ASIC join key), or None.

        Companies that changed names have multiple rows per ACN; we prefer the
        current-name row (``name_indicator = 'Y'``).
        """
        self.ensure_loaded()
        clean = _digits(acn)
        rows = self.query(
            f"SELECT * FROM {self.table_name} WHERE acn = ? "
            "ORDER BY (name_indicator = 'Y') DESC LIMIT 1",
            [clean],
        )
        return self._row_to_raw(rows[0]) if rows else None

    def lookup_abn(self, abn: str) -> RawRecord | None:
        """Return the canonical record matching an ABN, or None."""
        self.ensure_loaded()
        clean = _digits(abn)
        rows = self.query(
            f"SELECT * FROM {self.table_name} WHERE abn = ? "
            "ORDER BY (name_indicator = 'Y') DESC LIMIT 1",
            [clean],
        )
        return self._row_to_raw(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Filtered candidate slice
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """Pull a candidate slice.

        Supported params (all optional):
            status:        registration status (default ``"REGD"`` = active)
            entity_types:  list of ASIC Type codes, e.g. ``["APTY"]``
            min_years:     minimum years since registration_date
            state:         filter on previous_state (state of registration —
                           current operating state comes from the ABN spine)
            limit:         row cap (default 1000)
        """
        self.ensure_loaded()

        # Canonical current-name rows only — dedupes companies that were renamed.
        where: list[str] = ["name_indicator = 'Y'"]
        args: list[Any] = []

        status = params.get("status", _ACTIVE_STATUS)
        if status:
            where.append("status = ?")
            args.append(status)

        entity_types = params.get("entity_types")
        if entity_types:
            placeholders = ",".join("?" for _ in entity_types)
            where.append(f"type IN ({placeholders})")
            args.extend(entity_types)

        min_years = params.get("min_years")
        if min_years:
            where.append("registration_date IS NOT NULL")
            where.append("date_diff('year', registration_date, current_date) >= ?")
            args.append(int(min_years))

        state = params.get("state")
        if state:
            where.append("previous_state = ?")
            args.append(state)

        limit = int(params.get("limit", 1000))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.query(
            f"SELECT * FROM {self.table_name}{clause} LIMIT {limit}", args
        )
        return [self._row_to_raw(r) for r in rows]

    # ------------------------------------------------------------------
    # Normalise
    # ------------------------------------------------------------------

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import Age, CompanyRecord, Location, Ownership, Provenance

        acn = raw.get("acn")
        abn = raw.get("abn")
        # Prefer the ABN as the entity id (the resolver's join key); fall back to ACN.
        entity_id = f"abn:{abn}" if abn else f"acn:{acn}"

        type_code = (raw.get("entity_type_code") or "").upper()
        structure = _TYPE_STRUCTURE.get(type_code)

        reg = raw.get("status_effective_from")  # asic registration_date (ISO str)
        years = _years_since(reg)
        fetched_at = raw.get("fetched_at", "")

        def prov(field: str) -> Provenance:
            return Provenance(field=field, source=SOURCE_ID, fetched_at=fetched_at, confidence=0.95)

        return CompanyRecord(
            entity_id=entity_id,
            abn=abn or None,
            acn=acn or None,
            legal_name=raw.get("org_name"),
            country="Australia",
            location=Location(state=raw.get("state")),
            age=Age(abn_registered=reg, years_operating=years),
            ownership=Ownership(structure_guess=structure),
            provenance=[prov("acn"), prov("status"), prov("asic_registered"), prov("entity_type")],
        )

    # ------------------------------------------------------------------
    # Row → RawRecord mapping
    # ------------------------------------------------------------------

    def _row_to_raw(self, row: dict[str, Any]) -> RawRecord:
        reg = row.get("registration_date")
        reg_iso = reg.isoformat() if isinstance(reg, date) else (str(reg) if reg else None)
        # Prefer the current (most recent) name; fall back to the company_name column.
        name = row.get("current_name") or row.get("company_name")
        return RawRecord(
            source_id=SOURCE_ID,
            abn=row.get("abn") or None,
            acn=row.get("acn") or None,
            entity_type_code=row.get("type"),
            status_code=row.get("status"),
            status_effective_from=reg_iso,
            org_name=name,
            state=row.get("previous_state"),
            raw=dict(row),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _years_since(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        reg = date.fromisoformat(iso_date[:10])
    except ValueError:
        return None
    delta = date.today() - reg
    return max(0, delta.days // 365)
