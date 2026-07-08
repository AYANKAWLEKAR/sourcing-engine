"""InvenConnector — the paid MCP source that fills the ownership blind spots.

Inven is the only planned source for ``ownership.pe_vc_backed`` (the EXCLUDE rule
that is otherwise screened-but-never-filled) and ``ownership.institutional_on_register``,
plus a *direct* revenue estimate that beats the proxy under survivorship. It is
paid per-lookup, so it runs ``shortlist_only`` — via the ShortlistGate on the
top-N, never the full pool.

Transport: the fetch/normalize/enrich logic is complete and unit-tested with an
injected ``tool_caller``. The live MCP transport is built from ``INVEN_MCP_URL`` +
``INVEN_MCP_TOKEN`` when configured (a simple HTTP tool call — adjust
``_http_tool_caller`` to the real Inven MCP protocol when the server is connected).
When Inven is not configured, ``enrich_record`` degrades honestly: it flags the
record ``unverified:ownership:inven_not_configured`` rather than crashing or
silently passing PE/VC-backed companies.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base_mcp import MCPConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "inven"
# Direct data from a specialist provider — beats the proxy estimator (≤0.4).
CONFIDENCE = 0.9


def _http_tool_caller(url: str, token: str) -> Any:
    """A minimal HTTP MCP tool caller: POST {arguments} to ``{url}/tools/{tool}``.

    Placeholder transport for a hypothetical HTTP-exposed Inven MCP endpoint;
    swap for the real MCP client when the server protocol is known.
    """
    import httpx

    def call(server: str, tool: str, arguments: dict) -> dict:
        resp = httpx.post(
            f"{url.rstrip('/')}/tools/{tool}",
            json={"arguments": arguments},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    return call


class InvenConnector(MCPConnector):
    source_id: str = SOURCE_ID
    mcp_server: str = "inven"
    gate: str | None = "shortlist_only"

    @classmethod
    def from_settings(cls) -> InvenConnector:
        from ..config import get_settings

        s = get_settings()
        caller = (
            _http_tool_caller(s.inven_mcp_url, s.inven_mcp_token)
            if s.inven_mcp_url and s.inven_mcp_token
            else None
        )
        return cls(tool_caller=caller)

    @classmethod
    def from_settings_if_available(cls) -> InvenConnector | None:
        """A configured connector, or None when Inven creds are absent."""
        from ..config import get_settings

        s = get_settings()
        if s.inven_mcp_url and s.inven_mcp_token:
            return cls.from_settings()
        return None

    # ------------------------------------------------------------------
    # fetch / normalize
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:
        """Look up a company. ``params``: ``{"abn"}`` | ``{"company_name"}`` | ``{"domain"}``."""
        query = {
            k: v for k, v in params.items()
            if k in ("abn", "company_name", "domain") and v
        }
        if not query:
            return []
        result = self._call_mcp_tool(self.mcp_server, "search_companies", query)
        companies = result.get("companies") if isinstance(result, dict) else None
        if not isinstance(companies, list):
            companies = [result] if isinstance(result, dict) and result else []
        return [
            RawRecord(
                source_id=self.source_id,
                abn=c.get("abn"),
                org_name=c.get("name") or c.get("legal_name"),
                raw=c,
            )
            for c in companies
            if isinstance(c, dict)
        ]

    def normalize(self, raw: RawRecord) -> CompanyRecord:
        from ..models.company import CompanyRecord, Ownership, Provenance, Size

        c = raw.get("raw") or {}
        pe_vc = c.get("pe_vc_backed")
        if pe_vc is None:
            pe_vc = c.get("private_equity_backed") or c.get("vc_backed")
        institutional = c.get("institutional_investors") or c.get("institutional_on_register")
        revenue = c.get("revenue_aud") or c.get("revenue_est_aud") or c.get("revenue")

        prov = [
            Provenance(field="ownership.pe_vc_backed", source=SOURCE_ID,
                       fetched_at=raw.get("fetched_at", ""), confidence=CONFIDENCE),
        ]
        if revenue is not None:
            prov.append(Provenance(field="size.revenue_est_aud", source=SOURCE_ID,
                                   locator="inven_direct", confidence=CONFIDENCE))

        return CompanyRecord(
            entity_id=f"inven:{raw.get('abn') or _slug(raw.get('org_name'))}",
            abn=raw.get("abn"),
            legal_name=raw.get("org_name"),
            ownership=Ownership(
                pe_vc_backed=bool(pe_vc) if pe_vc is not None else None,
                institutional_on_register=bool(institutional) if institutional is not None else None,
            ),
            size=Size(
                revenue_est_aud=float(revenue) if revenue is not None else None,
                revenue_confidence=CONFIDENCE if revenue is not None else None,
            ),
            provenance=prov,
        )

    # ------------------------------------------------------------------
    # Shortlist-gate entry point
    # ------------------------------------------------------------------

    def enrich_record(self, record: CompanyRecord) -> CompanyRecord:
        """Merge Inven's ownership + direct revenue onto a shortlisted record.

        Degrades honestly when Inven isn't wired: flags the record rather than
        letting a PE/VC-backed company pass unchecked.
        """
        if self._tool_caller is None:
            record.flags.append("unverified:ownership:inven_not_configured")
            return record

        params = {"abn": record.abn} if record.abn else {"company_name": record.legal_name}
        raws = self.fetch(params)
        if not raws:
            record.flags.append("inven_checked_no_match")
            return record

        frag = self.normalize(raws[0])
        if frag.ownership.pe_vc_backed is not None:
            record.ownership.pe_vc_backed = frag.ownership.pe_vc_backed
        if frag.ownership.institutional_on_register is not None:
            record.ownership.institutional_on_register = frag.ownership.institutional_on_register
        if frag.size.revenue_est_aud is not None:
            # Direct estimate — beats the proxy under survivorship (higher confidence).
            record.size.revenue_est_aud = frag.size.revenue_est_aud
            record.size.revenue_confidence = frag.size.revenue_confidence
        record.provenance.extend(frag.provenance)
        return record


def _slug(value: str | None) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s or "unknown"
