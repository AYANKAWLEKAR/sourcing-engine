"""ASXListedConnector — ASX listed-companies roster as a DuckDB lookup (plan §3).

The ASX company directory CSV (manual download from asx.com.au, refreshed every
few months) is the roster of publicly listed Australian companies. Its job here
is a single screening signal: set ``ownership.listed_entity = True`` on matched
records so the ``listed_entity`` EXCLUDE in ``rank/screen.py`` actually fires.

Header drift note: the export format varies by ASX download version. The current
file carries ``ASX code, Company name, GICs industry group, Listing date,
Market Cap`` — no ACN. The loader resolves columns defensively (any column whose
name contains "code"/"name"/"acn") so a future export that adds ACN upgrades
matching from name-only (confidence 0.75) to deterministic ACN (0.95) with no
code change.

Matching (``is_listed``):
  * ACN digits match → (True, 0.95) — only possible when the export has ACNs.
  * normalized legal/trading name match (legal suffixes stripped, casefolded)
    → (True, 0.75), flagged ``asx_name_match_only`` by ``enrich_record``.
  * no match → (False, 0.0); ``enrich_record`` leaves ``listed_entity`` as None —
    absence from a name-only roster is weak evidence, never written as False.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR, get_settings
from .base_bulk import BulkConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "asx_listed_list"

ACN_CONFIDENCE = 0.95
NAME_CONFIDENCE = 0.75

# Same suffix vocabulary as the entity resolver, plus listed-company forms.
# Legal suffixes only — descriptive words (holdings, group) stay, otherwise
# "Example Holdings Ltd" would collapse to "example" and falsely match any
# private "Example Pty Ltd".
_SUFFIX = re.compile(
    r"\b(pty\s*ltd|pty\s*limited|limited|ltd|plc|nl|and co|inc|corp(oration)?)\s*$",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _norm_name(name: str | None) -> str:
    """Casefold, drop punctuation, strip trailing legal suffixes, collapse spaces."""
    if not name:
        return ""
    n = _PUNCT.sub(" ", name.lower())
    # Strip trailing suffix tokens repeatedly ("X Holdings Limited" -> "x").
    prev = None
    while prev != n:
        prev = n
        n = _SUFFIX.sub("", n.strip())
    return " ".join(n.split())


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


class ASXListedConnector(BulkConnector):
    source_id: str = SOURCE_ID
    table_name: str = "asx_listed"

    def __init__(self, db_path: Path | None = None, csv_path: str | None = None) -> None:
        super().__init__(db_path=db_path)
        self._csv_path = csv_path or get_settings().asx_csv_path or ""

    @classmethod
    def from_settings(cls) -> ASXListedConnector:
        return cls(csv_path=get_settings().asx_csv_path)

    @classmethod
    def from_settings_if_available(cls) -> ASXListedConnector | None:
        """A connector when an ASX CSV resolves (setting or data/ glob), else None.

        Lets production wiring skip the listed-entity check gracefully on
        machines without the manual download instead of failing mid-run.
        """
        connector = cls.from_settings()
        return connector if connector._resolve_csv_path() else None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _resolve_csv_path(self) -> str:
        """Explicit path, else the newest ASX_Listed_Companies_*.csv in data/."""
        if self._csv_path:
            return self._csv_path
        candidates = sorted(DATA_DIR.glob("ASX_Listed_Companies_*.csv"))
        if candidates:
            self._csv_path = str(candidates[-1])
        return self._csv_path

    def download(self) -> None:
        """Manual-download source: only validate the CSV is present."""
        path = self._resolve_csv_path()
        if not path or not Path(path).exists():
            raise FileNotFoundError(
                "ASX listed-companies CSV not found. Download the company directory "
                "from asx.com.au into data/ (ASX_Listed_Companies_*.csv) or set "
                f"ASX_CSV_PATH in .env (got: {path!r})."
            )

    def load(self) -> None:
        """Create ``asx_listed`` with defensively-resolved columns + normalized names."""
        csv = self._resolve_csv_path().replace("'", "''")
        self.conn.execute("DROP VIEW IF EXISTS _asx_src")
        self.conn.execute(
            f"""
            CREATE TEMP VIEW _asx_src AS
            SELECT * FROM read_csv(
                '{csv}',
                header=true, all_varchar=true,
                normalize_names=true, sample_size=-1, ignore_errors=true
            )
            """
        )
        cols = [r["column_name"] for r in self.query("DESCRIBE _asx_src")]

        def find(*fragments: str) -> str | None:
            for c in cols:
                if any(f in c.lower() for f in fragments):
                    return c
            return None

        code_col = find("code")
        name_col = find("name")
        if not code_col or not name_col:
            raise ValueError(
                f"ASX CSV headers not recognised (columns: {cols}). Expected a "
                "code column and a company-name column."
            )
        acn_col = find("acn")
        acn_expr = f"regexp_replace({acn_col}, '[^0-9]', '', 'g')" if acn_col else "NULL"

        self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")
        self.conn.execute(
            f"""
            CREATE TABLE {self.table_name} AS
            SELECT
                trim({code_col})  AS asx_code,
                trim({name_col})  AS company_name,
                {acn_expr}        AS acn,
                ''                AS normalized_name
            FROM _asx_src
            WHERE {code_col} IS NOT NULL AND {name_col} IS NOT NULL
            """
        )
        # Suffix-stripping lives in Python (_norm_name); ~2k rows, trivial.
        rows = self.query(f"SELECT asx_code, company_name FROM {self.table_name}")
        self.conn.executemany(
            f"UPDATE {self.table_name} SET normalized_name = ? WHERE asx_code = ?",
            [(_norm_name(r["company_name"]), r["asx_code"]) for r in rows],
        )
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_code ON {self.table_name}(asx_code)"
        )
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_name "
            f"ON {self.table_name}(normalized_name)"
        )

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _match(self, record: CompanyRecord) -> tuple[dict[str, Any], float, str] | None:
        """Best listing match for a record: (row, confidence, match_kind) or None."""
        self.ensure_loaded()
        if record.acn:
            rows = self.query(
                f"SELECT * FROM {self.table_name} WHERE acn = ? LIMIT 1",
                [_digits(record.acn)],
            )
            if rows:
                return rows[0], ACN_CONFIDENCE, "acn"
        for name in [record.legal_name, *record.trading_names]:
            norm = _norm_name(name)
            if not norm:
                continue
            rows = self.query(
                f"SELECT * FROM {self.table_name} WHERE normalized_name = ? LIMIT 1",
                [norm],
            )
            if rows:
                return rows[0], NAME_CONFIDENCE, "name"
        return None

    def is_listed(self, record: CompanyRecord) -> tuple[bool, float]:
        """(True, confidence) when the record matches an ASX listing, else (False, 0.0)."""
        match = self._match(record)
        return (True, match[1]) if match else (False, 0.0)

    def enrich_record(self, record: CompanyRecord) -> CompanyRecord:
        """Set ``ownership.listed_entity = True`` on match; leave None otherwise.

        Name-only roster absence is weak evidence, so a miss is never written
        as ``False`` — the screen only excludes on an explicit ``True``.
        """
        from ..models.company import Provenance

        match = self._match(record)
        if match is None:
            return record
        row, confidence, kind = match
        record.ownership.listed_entity = True
        record.provenance.append(
            Provenance(
                field="ownership.listed_entity",
                source=SOURCE_ID,
                locator=f"asx_code={row['asx_code']}; match={kind}",
                confidence=confidence,
            )
        )
        if kind == "name":
            record.flags.append("asx_name_match_only")
        return record

    # ------------------------------------------------------------------
    # SourceConnector contract
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """Lookup listings.

        Supported params (all optional):
            name:   normalized company-name match
            acn:    ACN digits match (only useful when the export carries ACNs)
            limit:  row cap (default 1000)
        """
        self.ensure_loaded()
        limit = int(params.get("limit", 1000))
        if params.get("acn"):
            rows = self.query(
                f"SELECT * FROM {self.table_name} WHERE acn = ? LIMIT {limit}",
                [_digits(params["acn"])],
            )
        elif params.get("name"):
            rows = self.query(
                f"SELECT * FROM {self.table_name} WHERE normalized_name = ? LIMIT {limit}",
                [_norm_name(params["name"])],
            )
        else:
            rows = self.query(f"SELECT * FROM {self.table_name} LIMIT {limit}")
        return [self._row_to_raw(r) for r in rows]

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import CompanyRecord, Ownership, Provenance

        code = (raw.get("raw") or {}).get("asx_code", "")
        acn = raw.get("acn")

        def prov(field: str) -> Provenance:
            return Provenance(
                field=field,
                source=SOURCE_ID,
                locator=f"asx_code={code}",
                fetched_at=raw.get("fetched_at", ""),
                confidence=ACN_CONFIDENCE,
            )

        return CompanyRecord(
            entity_id=f"asx:{code}",
            acn=acn or None,
            legal_name=raw.get("org_name"),
            country="Australia",
            ownership=Ownership(listed_entity=True),
            provenance=[prov("legal_name"), prov("ownership.listed_entity")],
        )

    def _row_to_raw(self, row: dict[str, Any]) -> RawRecord:
        return RawRecord(
            source_id=SOURCE_ID,
            acn=row.get("acn") or None,
            org_name=row.get("company_name"),
            raw=dict(row),
        )
