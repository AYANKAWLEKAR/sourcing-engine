"""SourceConnector — the interface all source connectors must implement (spec §6.1).

Every connector is a two-method object:
  ``fetch(params)``      → raw records off the source (HTTP/bulk file/etc.)
  ``normalize(raw)``     → a typed CompanyRecord for the candidate pool

Connectors are discovered at runtime from the SourceRegistry's ``connector_ref``
(e.g. ``connectors.abn.ABNLookupConnector``) and instantiated with settings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from ..models.company import CompanyRecord


class RawRecord(TypedDict, total=False):
    """Minimally-parsed record straight off a source, before normalisation.

    All fields are optional (total=False) — each source populates what it has.
    """

    source_id: str
    fetched_at: str  # ISO-8601 UTC timestamp

    # --- identifiers ---
    abn: str
    acn: str | None

    # --- entity type ---
    entity_type_code: str | None   # e.g. "PRV", "PUB", "IND", "TRT", "PTR"
    entity_description: str | None  # e.g. "Australian Private Company"

    # --- status ---
    status_code: str | None           # "Active" | "Cancelled"
    status_effective_from: str | None  # ISO date (YYYY-MM-DD)

    # --- name ---
    org_name: str | None       # main organisation name
    given_name: str | None     # for individuals / sole traders
    other_given_name: str | None
    family_name: str | None
    trading_names: list[str]   # all trading names collected from the source

    # --- location ---
    state: str | None     # state code: NSW, VIC, QLD, SA, WA, NT, ACT, TAS
    postcode: str | None  # 4-digit Australian postcode

    # --- audit ---
    raw: dict[str, Any]  # original parsed payload for audit / provenance


@runtime_checkable
class SourceConnector(Protocol):
    """Minimal interface all source connectors must satisfy (spec §6.1).

    ``@runtime_checkable`` so the registry/loader can assert that every
    concrete connector — built via one of the base classes — actually
    satisfies the contract (``isinstance(obj, SourceConnector)``).
    """

    source_id: str

    def fetch(self, params: dict) -> list[RawRecord]:
        """Pull raw records from this source.

        ``params`` is source-specific but follows the vocabulary in the
        SourcePlanItem (postcodes, ABN list, name query, …).
        """
        ...

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        """Convert a RawRecord to a validated CompanyRecord.

        Returns ``None``-equivalent (raise) for records that should be
        silently dropped (e.g. cancelled entities).
        """
        ...
