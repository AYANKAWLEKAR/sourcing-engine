"""ShortlistGate — expensive, gated enrichment for the ranked top-N (plan §2.2/§3).

Runs AFTER ranking, on the shortlist only. Two steps per RankedCompany:

1. **LinkedIn headcount** — only when the ``linkedin_headcount`` registry entry is
   ``enabled`` (it ships disabled: ToS-gated, Legal kill-switched). Fetched via the
   ConnectorRegistry seam; merges ``employee_count`` + provenance.
2. **ProxyEstimator** — always. Sets the low-confidence revenue/EBITDA band used by
   the PROXY_GATE flag, or records the honest
   ``unverified:ebitda_aud:no_employee_count`` flag when headcount is absent.

Then rebuilds each RankedCompany's ``deferred_assessment`` (the record changed).
The gate never re-scores — the locked statistical model and the judge blend are
untouched; this stage only enriches and re-documents.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from ..enrichment.proxy_estimator import ProxyEstimator
from ..rank.rank import deferred_items

if TYPE_CHECKING:
    from ..models.ranking import RankedCompany
    from ..models.source import SourceRegistryEntry

_LINKEDIN_SOURCE_ID = "linkedin_headcount"


class ShortlistGate:
    def __init__(
        self,
        registry_entries: list[SourceRegistryEntry],
        *,
        estimator: ProxyEstimator | None = None,
        connector_registry: Any = None,
        inven: Any = None,
        top_n: int = 10,
    ) -> None:
        self._entries = {e.source_id: e for e in registry_entries}
        self._estimator = estimator or ProxyEstimator()
        self._conn_registry = connector_registry
        # Paid MCP source (pe_vc / institutional / direct revenue). None unless
        # Inven creds are configured — then it runs on the top-N only.
        if inven is None:
            from ..connectors.inven import InvenConnector

            inven = InvenConnector.from_settings_if_available()
        self._inven = inven
        self.top_n = top_n

    def apply(self, shortlist: list[RankedCompany]) -> list[RankedCompany]:
        linkedin = self._linkedin_connector()
        for rc in shortlist[: self.top_n]:
            record = rc.record
            if linkedin is not None and record.size.employee_count is None:
                self._fetch_headcount(linkedin, rc)
            # Inven BEFORE proxy: its direct revenue survives the proxy guard, and
            # it fills pe_vc_backed (the otherwise-inert EXCLUDE) on the top-N.
            if self._inven is not None:
                try:
                    self._inven.enrich_record(record)
                except Exception as exc:  # noqa: BLE001
                    warnings.warn(f"ShortlistGate: inven enrich failed: {exc}", stacklevel=2)
                    record.flags.append("unverified:ownership:inven_fetch_failed")
            self._estimator.estimate(record)
            # The record changed — rebuild the open-questions checklist.
            rc.deferred_assessment = deferred_items(record)
        return shortlist

    # ------------------------------------------------------------------
    # LinkedIn (gated)
    # ------------------------------------------------------------------

    def _linkedin_connector(self) -> Any | None:
        entry = self._entries.get(_LINKEDIN_SOURCE_ID)
        if entry is None or not entry.enabled or not entry.connector_ref:
            return None  # disabled (the shipped default) → skip silently
        registry = self._conn_registry
        if registry is None:
            from ..connectors.connector_registry import ConnectorRegistry

            registry = ConnectorRegistry.get()
        try:
            return registry.get_or_create(entry.connector_ref)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"ShortlistGate: linkedin connector unavailable: {exc}", stacklevel=2)
            return None

    def _fetch_headcount(self, connector: Any, rc: RankedCompany) -> None:
        record = rc.record
        try:
            raws = connector.fetch({"companyName": record.legal_name})
            if not raws:
                record.flags.append("unverified:employee_count:no_linkedin_match")
                return
            normalized = connector.normalize(raws[0])
            if normalized.size.employee_count:
                record.size.employee_count = normalized.size.employee_count
                record.size.employee_source = normalized.size.employee_source or "linkedin"
                record.provenance.extend(normalized.provenance)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"ShortlistGate: linkedin fetch failed: {exc}", stacklevel=2)
            record.flags.append("unverified:employee_count:linkedin_fetch_failed")
