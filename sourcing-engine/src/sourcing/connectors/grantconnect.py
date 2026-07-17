"""GrantConnect bulk connector for Commonwealth grant-award evidence.

The public GrantConnect register does not expose a stable point-lookup API, so
this connector normalises configured official award CSVs and answers enrichment
lookups from DuckDB. CKAN resources are resolved at refresh time only where a
publisher exposes an actual downloadable CSV; protected help-page links are
explicitly marked ``access: manual`` in the source configuration.
It is intentionally enrichment-only: grants strengthen an existing candidate
identified elsewhere and never seed the discovery pool.
"""
from __future__ import annotations

import csv
import hashlib
import re
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml

from ..config import DATA_DIR, get_settings
from .base_bulk import BulkConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "grantconnect_awards"
_CKAN_PACKAGE_SHOW = "https://data.gov.au/data/api/3/action/package_show"

_DEFAULT_ALIASES: dict[str, list[str]] = {
    "recipient_name": ["Recipient Name", "Recipient", "Recipient Legal Name", "Company Name"],
    "recipient_abn": ["Recipient ABN", "Recipient ABN Number", "ABN", "ABN Number"],
    "program_name": ["Grant Program", "Program Name", "Grant Opportunity", "Funding Program"],
    "award_value_aud": ["Grant Award Value", "Grant Amount", "Grant Value", "Value", "Amount"],
    "award_date": ["Grant Award Date", "Agreement Start Date", "Date of Effect", "Start Date"],
    "recipient_state": ["Recipient State", "State", "State/Territory", "Recipient State/Territory"],
    "purpose_description": ["Grant Purpose", "Purpose", "Description", "Project Description"],
}
_REQUIRED_FIELDS = {"recipient_name", "recipient_abn", "award_value_aud", "award_date"}


class GrantConnectBulkConnector(BulkConnector):
    source_id = SOURCE_ID
    table_name = "grantconnect_awards"

    def __init__(
        self,
        db_path: Path | None = None,
        sources_path: Path | str | None = None,
        raw_dir: Path | str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(db_path=db_path)
        settings = get_settings()
        self._sources_path = Path(sources_path or settings.grantconnect_sources_path)
        self._raw_dir = Path(raw_dir or settings.grantconnect_raw_dir or DATA_DIR / "grantconnect")
        self._http = http_client
        self._parsed_rows: list[dict[str, Any]] = []
        self._downloaded: list[tuple[dict[str, Any], Path]] = []

    @classmethod
    def from_settings(cls) -> GrantConnectBulkConnector:
        settings = get_settings()
        return cls(sources_path=settings.grantconnect_sources_path, raw_dir=settings.grantconnect_raw_dir)

    # ------------------------------------------------------------------
    # CKAN download + parsing
    # ------------------------------------------------------------------

    def configured_sources(self) -> list[dict[str, Any]]:
        raw = yaml.safe_load(self._sources_path.read_text()) or {}
        sources = raw.get("sources") or []
        if not sources:
            raise ValueError(f"no GrantConnect sources in {self._sources_path}")
        return [dict(s) for s in sources]

    def resolve_resources(self) -> list[dict[str, Any]]:
        """Resolve each configured package to its current CSV resource."""
        close = self._http is None
        client = self._http or httpx.Client(timeout=60.0, follow_redirects=True)
        try:
            resolved: list[dict[str, Any]] = []
            for source in self.configured_sources():
                if source.get("file"):
                    continue  # offline fixtures / manually staged CSVs
                package_id = source.get("source_dataset")
                if not package_id:
                    raise ValueError("GrantConnect source requires source_dataset")
                response = client.get(_CKAN_PACKAGE_SHOW, params={"id": package_id})
                response.raise_for_status()
                payload = response.json()
                resources = (payload.get("result") or {}).get("resources") or []
                csv_resources = [
                    r for r in resources
                    if str(r.get("format") or "").upper() == "CSV" and r.get("url")
                ]
                if not csv_resources:
                    raise ValueError(f"no CSV resource available for GrantConnect dataset {package_id}")
                # The latest resource is conventionally last; CKAN's modified
                # timestamp gives a deterministic preference when it is present.
                resource = max(csv_resources, key=lambda r: str(r.get("last_modified") or r.get("created") or ""))
                resolved.append({**source, "resource_url": resource["url"], "resource_id": resource.get("id")})
            return resolved
        finally:
            if close:
                client.close()

    def download(self) -> None:
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._downloaded = []
        close = self._http is None
        client = self._http or httpx.Client(timeout=120.0, follow_redirects=True)
        try:
            sources = self.configured_sources()
            downloadable = [s for s in sources if not s.get("file") and s.get("access") != "manual"]
            remote = {r["source_dataset"]: r for r in self.resolve_resources()} if downloadable else {}
            for source in sources:
                local_file = source.get("file")
                if local_file:
                    path = Path(local_file)
                    if not path.is_absolute():
                        path = self._sources_path.parent / path
                    if not path.exists():
                        raise FileNotFoundError(path)
                    self._downloaded.append((source, path))
                    continue

                if source.get("access") == "manual":
                    raise RuntimeError(
                        f"{source['source_dataset']} is configured for manual staging: data.gov.au "
                        "currently links to a protected GrantConnect help page rather than a CSV. "
                        "Add a local `file` entry to grantconnect_sources.yaml before enabling it."
                    )
                resource = remote[source["source_dataset"]]
                digest = hashlib.sha256(resource["resource_url"].encode()).hexdigest()[:12]
                path = self._raw_dir / f"{source['source_dataset']}-{digest}.csv"
                response = client.get(resource["resource_url"])
                response.raise_for_status()
                path.write_bytes(response.content)
                self._downloaded.append((resource, path))
        finally:
            if close:
                client.close()

    def parse(self) -> None:
        if not self._downloaded:
            raise RuntimeError("GrantConnect parse called before download")
        rows: list[dict[str, Any]] = []
        for source, path in self._downloaded:
            with path.open("r", encoding="latin-1", newline="") as handle:
                reader = csv.DictReader(handle)
                mapping = _column_mapping(reader.fieldnames or [], source.get("column_aliases") or {})
                missing = _REQUIRED_FIELDS - mapping.keys()
                if missing:
                    raise ValueError(f"{path.name}: missing required GrantConnect columns {sorted(missing)}")
                unknown = set(reader.fieldnames or []) - set(mapping.values())
                if unknown:
                    warnings.warn(
                        f"{path.name}: unrecognised GrantConnect columns retained as source-only data: "
                        f"{sorted(unknown)[:8]}",
                        stacklevel=2,
                    )
                for raw in reader:
                    normalized = _normalize_row(raw, mapping, source)
                    if normalized is not None:
                        rows.append(normalized)
        self._parsed_rows = rows

    def load(self) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")
        self.conn.execute(
            f"""
            CREATE TABLE {self.table_name} (
                award_id VARCHAR PRIMARY KEY,
                recipient_name VARCHAR NOT NULL,
                recipient_abn VARCHAR NOT NULL,
                granting_agency VARCHAR NOT NULL,
                program_name VARCHAR,
                award_value_aud DOUBLE,
                award_date DATE,
                recipient_state VARCHAR,
                purpose_description VARCHAR,
                source_dataset VARCHAR NOT NULL,
                loaded_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        if self._parsed_rows:
            self.conn.executemany(
                f"""
                INSERT OR IGNORE INTO {self.table_name} (
                    award_id, recipient_name, recipient_abn, granting_agency,
                    program_name, award_value_aud, award_date, recipient_state,
                    purpose_description, source_dataset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["award_id"], r["recipient_name"], r["recipient_abn"], r["granting_agency"],
                        r["program_name"], r["award_value_aud"], r["award_date"], r["recipient_state"],
                        r["purpose_description"], r["source_dataset"],
                    )
                    for r in self._parsed_rows
                ],
            )
        self.conn.execute(f"CREATE INDEX idx_{self.table_name}_abn ON {self.table_name}(recipient_abn)")
        self.conn.execute(f"CREATE INDEX idx_{self.table_name}_date ON {self.table_name}(award_date)")

    # ------------------------------------------------------------------
    # Local lookup + CompanyRecord merge
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        abn = _digits(params.get("abn"))
        if len(abn) != 11 or not abn.isdigit() or not self.table_exists():
            return []
        return self.query(
            f"SELECT * FROM {self.table_name} WHERE recipient_abn = ? ORDER BY award_date DESC NULLS LAST",
            [abn],
        )

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import CompanyRecord, Provenance

        record = CompanyRecord(abn=raw.get("recipient_abn"), legal_name=raw.get("recipient_name"))
        record.moat_signals.gov_investment = True
        record.moat_signals.gov_grants_total_aud = int(raw.get("award_value_aud") or 0)
        record.moat_signals.gov_grants_count = 1
        record.moat_signals.gov_grant_programs = _one(raw.get("program_name"))
        record.moat_signals.gov_granting_agencies = _one(raw.get("granting_agency"))
        record.moat_signals.gov_grants_most_recent = raw.get("award_date")
        record.provenance.append(
            Provenance(
                field="moat_signals.gov_investment",
                source=SOURCE_ID,
                locator=raw.get("award_id"),
                fetched_at=datetime.now(tz=UTC).isoformat(),
                confidence=0.95,
            )
        )
        return record

    def enrich_record(self, record: CompanyRecord) -> CompanyRecord:
        from ..models.company import Provenance

        if not record.abn:
            return record
        rows = self.fetch({"abn": record.abn})
        if not rows:
            return record
        values = [float(r["award_value_aud"] or 0) for r in rows]
        m = record.moat_signals
        m.gov_investment = True
        m.gov_grants_total_aud = int(sum(values))
        m.gov_grants_count = len(rows)
        m.gov_grant_programs = sorted({r["program_name"] for r in rows if r.get("program_name")})
        m.gov_granting_agencies = sorted({r["granting_agency"] for r in rows if r.get("granting_agency")})
        dates = [r["award_date"] for r in rows if r.get("award_date")]
        m.gov_grants_most_recent = max(dates) if dates else None
        record.provenance.append(
            Provenance(
                field="moat_signals.gov_investment",
                source=SOURCE_ID,
                locator=f"recipientABN={record.abn}; {len(rows)} awards",
                fetched_at=datetime.now(tz=UTC).isoformat(),
                confidence=0.95,
            )
        )
        return record


def _column_mapping(headers: list[str], overrides: dict[str, list[str]]) -> dict[str, str]:
    by_normalized = {_normalise_column(header): header for header in headers if header}
    mapping: dict[str, str] = {}
    for target, defaults in _DEFAULT_ALIASES.items():
        aliases = [*(overrides.get(target) or []), *defaults]
        for alias in aliases:
            found = by_normalized.get(_normalise_column(alias))
            if found:
                mapping[target] = found
                break
    return mapping


def _normalize_row(raw: dict[str, str], mapping: dict[str, str], source: dict[str, Any]) -> dict[str, Any] | None:
    abn = _digits(raw.get(mapping["recipient_abn"]))
    if len(abn) != 11 or not abn.isdigit():
        return None
    recipient = _text(raw.get(mapping["recipient_name"]))
    if not recipient:
        return None
    award_date = _parse_date(raw.get(mapping["award_date"]))
    value = _parse_amount(raw.get(mapping["award_value_aud"]))
    agency = _text(source.get("granting_agency")) or "Commonwealth of Australia"
    program = _text(raw.get(mapping.get("program_name", "")))
    source_dataset = str(source.get("source_dataset") or source.get("file") or "grantconnect")
    key = "|".join([agency, abn, program or "", award_date.isoformat() if award_date else "", str(value or 0)])
    return {
        "award_id": hashlib.sha256(key.encode()).hexdigest(),
        "recipient_name": recipient,
        "recipient_abn": abn,
        "granting_agency": agency,
        "program_name": program,
        "award_value_aud": value,
        "award_date": award_date,
        "recipient_state": _text(raw.get(mapping.get("recipient_state", ""))),
        "purpose_description": _text(raw.get(mapping.get("purpose_description", ""))),
        "source_dataset": source_dataset,
    }


def _normalise_column(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _one(value: Any) -> list[str]:
    return [str(value)] if value else []


def _parse_amount(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(r"[^0-9.]", "", raw)
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -amount if negative else amount


def _parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None
