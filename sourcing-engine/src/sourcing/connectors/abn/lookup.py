"""ABNLookupAPIConnector — live ABN Lookup API on the APIConnector base (plan §3.5).

This is the **resolution bridge**: it turns a scraped, ABN-less record (a name +
postcode + state) into an ABN, and looks up full detail for a known ABN. It is an
``APIConnector`` (live, rate-limited, cached) — NOT a ``BulkConnector``; there is
nothing to download, each query is one live request.

Two endpoints (both JSONP — the ``callback({...})`` wrapper is stripped by the
APIConnector's ``_get``):
  * Detail:    ``/json/AbnDetails.aspx?abn={abn}&guid={GUID}``
  * Name match: ``/json/MatchingNames.aspx?name={name}&maxResults=20&guid={GUID}``

Two fetch modes:
  * ``{"abn": "51824753556"}``            → one detail record
  * ``{"name": "Xero", "state": "VIC"}``  → up to 20 scored name-match candidates

Credential: ``ABN_LOOKUP_GUID`` from ``.env`` (free; register at
abr.business.gov.au/Tools/WebServices). Missing GUID raises on construction.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import get_settings
from ..base_api import APIConnector
from ..protocol import RawRecord
from .parser import _LISTED_CODES, _STRUCTURE_MAP, calc_years_operating

if TYPE_CHECKING:
    from ...models.company import CompanyRecord

SOURCE_ID = "abn_lookup_api"

_DETAIL_URL = "https://abr.business.gov.au/json/AbnDetails.aspx"
_NAMES_URL = "https://abr.business.gov.au/json/MatchingNames.aspx"


class ABNLookupAPIConnector(APIConnector):
    source_id: str = SOURCE_ID
    base_url: str = _DETAIL_URL
    rate_limit_rps: float = 4.0          # plan §3.5: ≤ ~4 req/s
    cache_ttl_seconds: int = 7 * 24 * 3600  # plan §3.5: 7-day TTL

    def __init__(self, guid: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._guid = guid if guid is not None else get_settings().abn_lookup_guid
        if not self._guid:
            raise ValueError(
                "ABN_LOOKUP_GUID is required. "
                "Register (free) at https://abr.business.gov.au/Tools/WebServices"
            )

    @classmethod
    def from_settings(cls, **kwargs: Any) -> ABNLookupAPIConnector:
        return cls(guid=get_settings().abn_lookup_guid, **kwargs)

    # ------------------------------------------------------------------
    # SourceConnector contract
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        if "abn" in params:
            return self._detail(params["abn"])
        if "name" in params:
            return self._match_names(params["name"], params.get("state"))
        raise ValueError(
            f"Unsupported params: {list(params.keys())}. Expected 'abn' or 'name'."
        )

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ...models.company import Age, CompanyRecord, Location, Ownership, Provenance

        abn = raw.get("abn") or ""
        entity_id = f"abn:{abn}" if abn else f"name:{raw.get('org_name', '')}"

        type_code = raw.get("entity_type_code") or ""
        structure = _STRUCTURE_MAP.get(type_code)
        listed = type_code in _LISTED_CODES

        reg = raw.get("status_effective_from")
        years = calc_years_operating(reg)
        fetched_at = raw.get("fetched_at", "")

        def prov(field: str) -> Provenance:
            return Provenance(field=field, source=SOURCE_ID, fetched_at=fetched_at, confidence=0.95)

        return CompanyRecord(
            entity_id=entity_id,
            abn=abn or None,
            acn=raw.get("acn"),
            legal_name=raw.get("org_name"),
            trading_names=list(raw.get("trading_names") or []),
            country="Australia",
            location=Location(state=raw.get("state"), postcode=raw.get("postcode")),
            age=Age(abn_registered=reg, years_operating=years),
            ownership=Ownership(structure_guess=structure, listed_entity=listed or None),
            provenance=[prov("abn"), prov("legal_name"), prov("state"), prov("years_operating")],
        )

    # ------------------------------------------------------------------
    # Endpoint helpers
    # ------------------------------------------------------------------

    def _detail(self, abn: str) -> list[RawRecord]:
        data = self._get(_DETAIL_URL, params={"abn": _digits(abn), "guid": self._guid})
        rec = _detail_to_raw(data)
        return [rec] if rec is not None else []

    def _match_names(self, name: str, state: str | None) -> list[RawRecord]:
        params = {"name": name, "maxResults": 20, "guid": self._guid}
        if state:
            params["state"] = state
        data = self._get(_NAMES_URL, params=params)
        return _names_to_raw(data)


# ---------------------------------------------------------------------------
# JSON → RawRecord mapping
# ---------------------------------------------------------------------------

def _detail_to_raw(data: dict) -> RawRecord | None:
    """Map an ``AbnDetails`` JSON object to a RawRecord (or None if not found)."""
    abn = (data.get("Abn") or "").strip()
    if not abn:
        # ABR puts an explanation in Message when nothing matched.
        return None
    return RawRecord(
        source_id=SOURCE_ID,
        abn=abn,
        acn=(data.get("Acn") or None),
        entity_type_code=data.get("EntityTypeCode"),
        entity_description=data.get("EntityTypeName"),
        status_code=data.get("AbnStatus"),
        status_effective_from=data.get("AbnStatusEffectiveFrom"),
        org_name=data.get("EntityName"),
        trading_names=list(data.get("BusinessName") or []),
        state=data.get("AddressState"),
        postcode=data.get("AddressPostcode"),
        raw=dict(data),
    )


def _names_to_raw(data: dict) -> list[RawRecord]:
    """Map a ``MatchingNames`` JSON object to a list of candidate RawRecords.

    Each candidate carries its match ``Score`` in ``raw`` for the resolver to use.
    """
    out: list[RawRecord] = []
    for cand in data.get("Names") or []:
        abn = (cand.get("Abn") or "").strip()
        if not abn:
            continue
        out.append(
            RawRecord(
                source_id=SOURCE_ID,
                abn=abn,
                acn=None,
                status_code=cand.get("AbnStatus"),
                org_name=cand.get("Name"),
                state=cand.get("State"),
                postcode=cand.get("Postcode"),
                raw=dict(cand),
            )
        )
    return out


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())
