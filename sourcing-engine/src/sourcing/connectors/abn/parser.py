"""ABR XML response parser: XML bytes → list[RawRecord].

Handles two response shapes returned by the ABN Lookup API:
  * ``businessEntity``    — detail record from ABRSearchByABN / ABRSearchByASIC
  * ``searchResultsRecord`` — summary record from SearchByPostcode / name search

Both are normalised into a ``RawRecord`` dict so the connector's
``normalize()`` method sees a single shape regardless of which endpoint
was called.

The ABR API wraps all responses in the default namespace
``http://abr.business.gov.au/ABRXMLSearch/``.  ElementTree requires this to
be prefixed on every tag lookup, so we use the ``_t()`` helper throughout.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from typing import Any

from ..protocol import RawRecord

# Default XML namespace for all ABR API responses.
_NS = "http://abr.business.gov.au/ABRXMLSearch/"
_P = f"{{{_NS}}}"  # prefix for ElementTree tag matching

# Entity type codes that signal a publicly listed company (used to hint
# listed_entity=True in normalisation; confirmed separately via ASX source).
_LISTED_CODES: frozenset[str] = frozenset({"PUB"})

# Entity type codes that map to ownership structure guesses.
_STRUCTURE_MAP: dict[str, str] = {
    "IND": "sole-trader",
    "SOL": "sole-trader",
    "PTR": "partnership",
    "PRV": "private-company",
    "PUB": "public-company",
    "TRT": "trust",
    "ASS": "association",
    "STG": "government",
    "CTH": "government",
    "LCL": "government",
}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _tag(elem: ET.Element, *path: str) -> ET.Element | None:
    """Walk a sequence of child tag names under ``elem``, respecting the NS."""
    cur: ET.Element | None = elem
    for tag in path:
        if cur is None:
            return None
        cur = cur.find(f"{_P}{tag}")
    return cur


def _t(elem: ET.Element, *path: str) -> str | None:
    """Return the text of the element at ``path`` (or None if absent/empty)."""
    node = _tag(elem, *path)
    if node is None:
        return None
    text = (node.text or "").strip()
    return text if text else None


def _all(elem: ET.Element, tag: str) -> list[ET.Element]:
    """Return all direct children with ``tag``."""
    return elem.findall(f"{_P}{tag}")


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def calc_years_operating(date_str: str | None) -> int | None:
    """Calculate years operating from an ISO date string.

    The ABR returns dates like ``2001-07-01`` or the sentinel ``0001-01-01``
    for unknown/not-applicable dates.  We return None for sentinel values.
    """
    if not date_str or date_str.startswith("0001"):
        return None
    try:
        reg = date.fromisoformat(date_str[:10])
        delta = date.today() - reg
        return max(0, math.floor(delta.days / 365.25))
    except ValueError:
        return None


def _trading_names(entity: ET.Element) -> list[str]:
    """Collect all trading name strings from an entity element."""
    names: list[str] = []
    for tag in ("mainTradingName", "otherTradingName"):
        for el in _all(entity, tag):
            name = _t(el, "organisationName")
            if name:
                names.append(name)
    return names


def _raw_dict(entity: ET.Element) -> dict[str, Any]:
    """Serialise ``entity`` to a plain dict for audit storage."""
    def _elem_to_dict(e: ET.Element) -> Any:
        children = list(e)
        if not children:
            return e.text or ""
        return {c.tag.replace(f"{_P}", ""): _elem_to_dict(c) for c in children}

    return _elem_to_dict(entity)


# ---------------------------------------------------------------------------
# Public parsers
# ---------------------------------------------------------------------------

def parse_response(xml_bytes: bytes, source_id: str = "abn_lookup_api") -> list[RawRecord]:
    """Entry point: parse a full ABR API response into ``RawRecord`` items.

    Automatically detects the response shape (detail vs search-results) and
    delegates to the appropriate parser.  Raises ``ABRException`` if the API
    returned an error element.
    """
    root = ET.fromstring(xml_bytes)
    response = _tag(root, "response")
    if response is None:
        # Some endpoints return the payload at the root level directly.
        response = root

    # Error case — the API embeds an <exception> instead of results.
    exc_desc = _t(response, "exception", "exceptionDescription")
    if exc_desc:
        raise ABRException(exc_desc)

    fetched_at = _now_iso()

    # Detail response: single <businessEntity>
    entity = _tag(response, "businessEntity")
    if entity is not None:
        rec = _parse_entity(entity, source_id, fetched_at)
        return [rec] if rec is not None else []

    # Search response: <searchResultsList> with multiple <searchResultsRecord>
    results_list = _tag(response, "searchResultsList")
    if results_list is not None:
        records: list[RawRecord] = []
        for rec_el in _all(results_list, "searchResultsRecord"):
            rec = _parse_result_record(rec_el, source_id, fetched_at)
            if rec is not None:
                records.append(rec)
        return records

    return []


def _parse_entity(entity: ET.Element, source_id: str, fetched_at: str) -> RawRecord | None:
    """Parse a full ``<businessEntity>`` element (from ABRSearchByABN)."""
    abn = _t(entity, "ABN", "identifierValue")
    if not abn:
        return None

    status_code = _t(entity, "entityStatus", "entityStatusCode")
    # Only return active entities — cancelled ones are not acquisition candidates.
    if status_code and status_code.lower() == "cancelled":
        return None

    status_from = _t(entity, "entityStatus", "effectiveFrom")

    # Name — organisations have <mainName>, individuals have <legalName>.
    org_name = _t(entity, "mainName", "organisationName")
    given_name = _t(entity, "legalName", "givenName")
    other_given = _t(entity, "legalName", "otherGivenName")
    family_name = _t(entity, "legalName", "familyName")

    return RawRecord(
        source_id=source_id,
        fetched_at=fetched_at,
        abn=abn,
        acn=_t(entity, "ASICNumber"),
        entity_type_code=_t(entity, "entityType", "entityTypeCode"),
        entity_description=_t(entity, "entityType", "entityDescription"),
        status_code=status_code,
        status_effective_from=status_from,
        org_name=org_name,
        given_name=given_name,
        other_given_name=other_given,
        family_name=family_name,
        trading_names=_trading_names(entity),
        state=_t(entity, "mainBusinessPhysicalAddress", "stateCode"),
        postcode=_t(entity, "mainBusinessPhysicalAddress", "postcode"),
        raw=_raw_dict(entity),
    )


def _parse_result_record(rec: ET.Element, source_id: str, fetched_at: str) -> RawRecord | None:
    """Parse a summary ``<searchResultsRecord>`` element (from postcode/name search)."""
    abn = _t(rec, "ABN", "identifierValue")
    if not abn:
        return None

    # Filter cancelled at parse time.
    status_code = _t(rec, "ABNStatus")
    if status_code and status_code.lower() == "cancelled":
        return None

    status_from = _t(rec, "ABNStatusEffectiveFrom")
    org_name = _t(rec, "mainName", "organisationName")
    given_name = _t(rec, "legalName", "givenName")
    other_given = _t(rec, "legalName", "otherGivenName")
    family_name = _t(rec, "legalName", "familyName")

    return RawRecord(
        source_id=source_id,
        fetched_at=fetched_at,
        abn=abn,
        acn=None,  # not in summary records
        entity_type_code=_t(rec, "entityType", "entityTypeCode"),
        entity_description=_t(rec, "entityType", "entityDescription"),
        status_code=status_code,
        status_effective_from=status_from,
        org_name=org_name,
        given_name=given_name,
        other_given_name=other_given,
        family_name=family_name,
        trading_names=_trading_names(rec),
        state=_t(rec, "mainBusinessPhysicalAddress", "stateCode"),
        postcode=_t(rec, "mainBusinessPhysicalAddress", "postcode"),
        raw=_raw_dict(rec),
    )


def normalize_to_company_record(raw: RawRecord):
    """Convert a ``RawRecord`` from the ABN API into a ``CompanyRecord``.

    Imported here (not at module level) to avoid circular imports — connectors
    are below models in the dependency graph.
    """
    from ...models.company import (
        Age,
        CompanyRecord,
        Location,
        Ownership,
        Provenance,
    )

    abn = raw.get("abn", "")
    entity_id = f"abn:{abn}"
    fetched_at = raw.get("fetched_at", "")
    source_id = raw.get("source_id", "abn_lookup_api")

    # Resolve name.
    org_name = raw.get("org_name")
    if org_name:
        legal_name = org_name
    else:
        parts = filter(None, [raw.get("given_name"), raw.get("other_given_name"), raw.get("family_name")])
        legal_name = " ".join(parts) or None

    trading_names: list[str] = list(raw.get("trading_names") or [])

    # Ownership inference from entity type code.
    type_code = raw.get("entity_type_code") or ""
    structure_guess = _STRUCTURE_MAP.get(type_code)
    listed_hint = type_code in _LISTED_CODES

    status_from = raw.get("status_effective_from")
    years = calc_years_operating(status_from)

    def prov(field: str) -> Provenance:
        return Provenance(field=field, source=source_id, fetched_at=fetched_at, confidence=0.95)

    return CompanyRecord(
        entity_id=entity_id,
        abn=abn or None,
        acn=raw.get("acn"),
        legal_name=legal_name,
        trading_names=trading_names,
        country="Australia",
        location=Location(
            state=raw.get("state"),
            postcode=raw.get("postcode"),
        ),
        age=Age(
            abn_registered=status_from,
            years_operating=years,
        ),
        ownership=Ownership(
            structure_guess=structure_guess,
            listed_entity=listed_hint if listed_hint else None,
        ),
        provenance=[
            prov("abn"),
            prov("legal_name"),
            prov("state"),
            prov("years_operating"),
        ],
    )


class ABRException(RuntimeError):
    """Raised when the ABR API returns an ``<exception>`` element."""
