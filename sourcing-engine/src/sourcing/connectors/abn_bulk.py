"""ABNBulkExtractConnector — the ABR bulk extract as the full-register spine (plan §3).

The free ABN Bulk Extract on data.gov.au covers *every* Australian business —
sole traders, partnerships, and trusts included — where the ASIC company
dataset only covers ACN-registered companies. It closes the ~14% of resolved
records that carry a valid ABN but have no ASIC row, giving them legal name,
operating state/postcode, entity type, and registration date.

Data shape: two zips (``public_split_1_10.zip``, ``public_split_11_20.zip``),
each holding ~10 XML files, ~500MB unpacked apiece. ``download()`` validates
local copies (or CKAN-resolves and streams them down when
``ABN_BULK_DOWNLOAD=true``); ``load()`` stream-parses ``<ABR>`` elements
straight out of the zip members with ``iterparse`` — nothing is unpacked to
disk, and ``elem.clear()`` keeps memory flat across ~20M records.

Unlike ASIC (whose ``previous_state`` is state of *incorporation* — audit fix 3),
the ABR ``BusinessAddress`` is the entity's operating address, so ``state`` /
``postcode`` are legitimate ``fetch`` filters here.
"""
from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR, get_settings
from .base_bulk import BulkConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    from ..models.company import CompanyRecord

SOURCE_ID = "abn_bulk_extract"

CKAN_PACKAGE_URL = (
    "https://data.gov.au/data/api/3/action/package_show?id=abn-bulk-extract"
)
ZIP_NAMES = ("public_split_1_10.zip", "public_split_11_20.zip")

# Active-registration ABN status code ("CAN" = cancelled).
_ACTIVE_STATUS = "ACT"

# The orchestrator's spine params may carry ASIC Type codes when a plan mixes
# spine sources; translate the common ones to their ABR equivalents.
_ASIC_TO_ABR_TYPE: dict[str, str] = {"APTY": "PRV", "APUB": "PUB"}


class ABNBulkExtractConnector(BulkConnector):
    source_id: str = SOURCE_ID
    table_name: str = "abn_extract"

    def __init__(
        self,
        db_path: Path | None = None,
        zip_paths: list[str] | None = None,
        data_dir: str | None = None,
        allow_download: bool | None = None,
    ) -> None:
        super().__init__(db_path=db_path)
        settings = get_settings()
        self._zip_paths = [str(p) for p in zip_paths] if zip_paths else []
        self._data_dir = Path(data_dir or settings.abn_bulk_dir or (DATA_DIR / "abn_bulk"))
        self._allow_download = (
            settings.abn_bulk_download if allow_download is None else allow_download
        )

    @classmethod
    def from_settings(cls) -> ABNBulkExtractConnector:
        return cls()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _resolve_zip_paths(self) -> list[str]:
        """Explicit paths, else every public_split_*.zip under the data dir."""
        if self._zip_paths:
            return self._zip_paths
        found = sorted(self._data_dir.glob("public_split_*.zip"))
        if found:
            self._zip_paths = [str(p) for p in found]
        return self._zip_paths

    def download(self) -> None:
        """Validate local zips; CKAN-resolve + stream-download when allowed.

        The two zips total ~1.7GB — the live download only runs when
        ``ABN_BULK_DOWNLOAD=true`` (or ``allow_download=True``), so importing or
        instantiating this connector can never trigger it by surprise.
        """
        if self._resolve_zip_paths():
            missing = [p for p in self._zip_paths if not Path(p).exists()]
            if not missing:
                return
            raise FileNotFoundError(f"ABN bulk zip(s) not found: {missing}")
        if not self._allow_download:
            raise FileNotFoundError(
                "ABN bulk extract zips not found. Download public_split_1_10.zip and "
                f"public_split_11_20.zip from data.gov.au into {self._data_dir} "
                "(dataset 'ABN Bulk Extract'), or set ABN_BULK_DOWNLOAD=true to "
                "CKAN-resolve and download them (~1.7GB)."
            )
        self._zip_paths = self._download_from_ckan()

    def _download_from_ckan(self) -> list[str]:
        """Resolve the zip resource URLs via CKAN and stream them to the data dir."""
        import httpx

        resp = httpx.get(CKAN_PACKAGE_URL, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        resources = (resp.json().get("result") or {}).get("resources") or []
        urls: dict[str, str] = {}
        for res in resources:
            url = res.get("url") or ""
            basename = url.rsplit("/", 1)[-1].lower()
            if basename in ZIP_NAMES:
                urls[basename] = url
        if len(urls) != len(ZIP_NAMES):
            raise RuntimeError(
                f"CKAN package did not expose the expected zips {ZIP_NAMES}; "
                f"found: {sorted(urls)}"
            )

        self._data_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for name in ZIP_NAMES:
            target = self._data_dir / name
            if not target.exists():
                part = target.with_suffix(".part")
                with httpx.stream(
                    "GET", urls[name], timeout=600.0, follow_redirects=True
                ) as stream:
                    stream.raise_for_status()
                    with part.open("wb") as fh:
                        for chunk in stream.iter_bytes(1 << 20):
                            fh.write(chunk)
                part.rename(target)
            paths.append(str(target))
        return paths

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Stream every <ABR> record to a temp CSV, then bulk-load via read_csv.

        Row-by-row ``INSERT OR REPLACE`` against the ABN primary key was
        pathologically slow on the full register (~460 rows/s — the unique-index
        upsert cost grows with the table, projecting to ~12h for ~20M rows).
        Instead we stream-parse to a temp CSV (flat, memory-safe) and let DuckDB's
        parallel ``read_csv`` do the heavy load in one shot, de-duplicating by ABN
        in SQL (last-updated wins). Orders-of-magnitude faster, same result.
        """
        import csv
        import os
        import tempfile

        cols = [
            "abn", "acn", "entity_type_code", "entity_type_text", "legal_name",
            "trading_names", "state", "postcode", "abn_status",
            "abn_status_from", "record_last_updated",
        ]
        fd, tmp_csv = tempfile.mkstemp(suffix=".csv", prefix="abn_extract_")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(cols)
                for zip_path in self._resolve_zip_paths():
                    for row in self._iter_abr_records(Path(zip_path)):
                        writer.writerow(row)

            tmp_sql = tmp_csv.replace("'", "''")
            self.conn.execute(f"DROP TABLE IF EXISTS {self.table_name}")
            self.conn.execute(
                f"""
                CREATE TABLE {self.table_name} AS
                SELECT
                    abn, acn, entity_type_code, entity_type_text, legal_name,
                    trading_names, state, postcode, abn_status,
                    try_strptime(abn_status_from, '%Y-%m-%d')::DATE AS abn_status_from,
                    try_strptime(record_last_updated, '%Y-%m-%d')::DATE AS record_last_updated
                FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY abn ORDER BY record_last_updated DESC NULLS LAST
                    ) AS _rn
                    FROM read_csv(
                        '{tmp_sql}', header=true, all_varchar=true,
                        sample_size=-1, quote='"', escape='"'
                    )
                ) WHERE _rn = 1
                """
            )
        finally:
            os.unlink(tmp_csv)

        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_acn ON {self.table_name}(acn)"
        )
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_geo "
            f"ON {self.table_name}(state, postcode)"
        )

    def _iter_abr_records(self, zip_path: Path) -> Iterator[tuple]:
        """Stream (insert-tuple) rows for every <ABR> element in a zip's XML members.

        The bulk-extract XML is NOT namespaced (unlike the ABN Lookup API's
        ``abn/parser.py``). ``elem.clear()`` after each record is mandatory —
        without it iterparse accumulates the whole ~500MB document in memory.
        """
        import xml.etree.ElementTree as ET

        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if not member.lower().endswith(".xml"):
                    continue
                with zf.open(member) as fh:
                    for _event, elem in ET.iterparse(fh, events=("end",)):
                        if elem.tag != "ABR":
                            continue
                        row = self._abr_to_row(elem)
                        elem.clear()
                        if row is not None:
                            yield row

    @staticmethod
    def _abr_to_row(elem: ET.Element) -> tuple | None:
        abn_el = elem.find("ABN")
        abn = _digits(abn_el.text or "") if abn_el is not None else ""
        if not abn:
            return None
        status = abn_el.get("status") if abn_el is not None else None
        status_from = _yyyymmdd_to_iso(
            abn_el.get("ABNStatusFromDate") if abn_el is not None else None
        )

        ent = elem.find("EntityType")
        type_code = _text(ent, "EntityTypeInd")
        type_text = _text(ent, "EntityTypeText")

        # Non-individuals carry MainEntity; individuals carry LegalEntity.
        holder = elem.find("MainEntity")
        if holder is None:
            holder = elem.find("LegalEntity")
        name = _text(holder, "NonIndividualName/NonIndividualNameText")
        if not name and holder is not None:
            ind = holder.find("IndividualName")
            if ind is not None:
                # Per the bulk-extract XSD: GivenName occurs 0–2 times, then FamilyName.
                parts = [
                    g.text.strip() for g in ind.findall("GivenName") if g.text
                ]
                family = _text(ind, "FamilyName")
                if family:
                    parts.append(family)
                name = " ".join(p for p in parts if p) or None
        state = _text(holder, "BusinessAddress/AddressDetails/State")
        postcode = _text(holder, "BusinessAddress/AddressDetails/Postcode")

        acn = _digits(_text(elem, "ASICNumber") or "") or None
        trading = [
            t.text.strip()
            for t in elem.findall("OtherEntity/NonIndividualName/NonIndividualNameText")
            if t.text and t.text.strip()
        ]
        updated = _yyyymmdd_to_iso(elem.get("recordLastUpdatedDate"))

        return (
            abn, acn, type_code, type_text, name, json.dumps(trading),
            state, postcode, status, status_from, updated,
        )

    # ------------------------------------------------------------------
    # Point lookup
    # ------------------------------------------------------------------

    def lookup_abn(self, abn: str) -> RawRecord | None:
        """Indexed point lookup on the ABN primary key, or None."""
        self.ensure_loaded()
        rows = self.query(
            f"SELECT * FROM {self.table_name} WHERE abn = ?", [_digits(abn)]
        )
        return self._row_to_raw(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Filtered candidate slice
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """Pull a candidate slice.

        Supported params (all optional; compatible with the orchestrator's
        spine output ``{limit, min_years?, entity_types?}``):
            abn_status:    ABN status (default ``"ACT"`` = active)
            entity_types:  ABR type codes, e.g. ``["IND", "PRV"]`` (ASIC codes
                           ``APTY``/``APUB`` are translated)
            min_years:     minimum years since abn_status_from
            state:         operating state (BusinessAddress — unlike ASIC)
            postcode:      operating postcode
            limit:         row cap (default 1000)
        """
        self.ensure_loaded()
        where: list[str] = []
        args: list[Any] = []

        status = params.get("abn_status", _ACTIVE_STATUS)
        if status:
            where.append("abn_status = ?")
            args.append(status)

        entity_types = params.get("entity_types")
        if entity_types:
            translated = [_ASIC_TO_ABR_TYPE.get(t, t) for t in entity_types]
            placeholders = ",".join("?" for _ in translated)
            where.append(f"entity_type_code IN ({placeholders})")
            args.extend(translated)

        min_years = params.get("min_years")
        if min_years:
            where.append("abn_status_from IS NOT NULL")
            where.append("date_diff('year', abn_status_from, current_date) >= ?")
            args.append(int(min_years))

        state = params.get("state")
        if state:
            where.append("state = ?")
            args.append(state)

        postcode = params.get("postcode")
        if postcode:
            where.append("postcode = ?")
            args.append(str(postcode))

        limit = int(params.get("limit", 1000))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.query(f"SELECT * FROM {self.table_name}{clause} LIMIT {limit}", args)
        return [self._row_to_raw(r) for r in rows]

    # ------------------------------------------------------------------
    # Normalise
    # ------------------------------------------------------------------

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import Age, CompanyRecord, Location, Ownership, Provenance
        from .abn.parser import _STRUCTURE_MAP

        abn = raw.get("abn")
        type_code = (raw.get("entity_type_code") or "").upper()
        reg = raw.get("status_effective_from")
        fetched_at = raw.get("fetched_at", "")

        def prov(field: str) -> Provenance:
            return Provenance(
                field=field, source=SOURCE_ID, fetched_at=fetched_at, confidence=0.95
            )

        return CompanyRecord(
            entity_id=f"abn:{abn}",
            abn=abn,
            acn=raw.get("acn") or None,
            legal_name=raw.get("org_name"),
            trading_names=list(raw.get("trading_names") or []),
            country="Australia",
            location=Location(state=raw.get("state"), postcode=raw.get("postcode")),
            age=Age(abn_registered=reg, years_operating=_years_since(reg)),
            ownership=Ownership(structure_guess=_STRUCTURE_MAP.get(type_code)),
            provenance=[
                prov("abn"), prov("legal_name"), prov("state"),
                prov("abn_registered"), prov("entity_type"),
            ],
        )

    # ------------------------------------------------------------------
    # Row → RawRecord mapping
    # ------------------------------------------------------------------

    def _row_to_raw(self, row: dict[str, Any]) -> RawRecord:
        reg = row.get("abn_status_from")
        reg_iso = reg.isoformat() if isinstance(reg, date) else (str(reg) if reg else None)
        try:
            trading = json.loads(row.get("trading_names") or "[]")
        except (TypeError, ValueError):
            trading = []
        return RawRecord(
            source_id=SOURCE_ID,
            abn=row.get("abn"),
            acn=row.get("acn") or None,
            entity_type_code=row.get("entity_type_code"),
            entity_description=row.get("entity_type_text"),
            status_code=row.get("abn_status"),
            status_effective_from=reg_iso,
            org_name=row.get("legal_name"),
            trading_names=trading,
            state=row.get("state"),
            postcode=row.get("postcode"),
            raw=dict(row),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _text(parent: ET.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(path)
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _yyyymmdd_to_iso(value: str | None) -> str | None:
    """``"20000201"`` → ``"2000-02-01"``; None on anything malformed."""
    if not value or len(value) != 8 or not value.isdigit():
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _years_since(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        reg = date.fromisoformat(iso_date[:10])
    except ValueError:
        return None
    delta = date.today() - reg
    return max(0, delta.days // 365)
