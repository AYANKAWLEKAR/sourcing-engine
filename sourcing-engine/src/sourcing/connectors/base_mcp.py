"""MCPConnector — base class for MCP-server-backed sources (plan §2.6).

Concrete MCP connectors (Inven — shortlist-gated) inherit from this, set
``mcp_server``, and implement ``fetch``/``normalize`` by calling
``_call_mcp_tool(server, tool, arguments)``.

The MCP transport is stubbed in Step 1 (interface only) and completed when the
MCP server is connected — see plan §3.14. The base class exists now so the
hierarchy is complete and the loader/registry tests cover all five mechanisms.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord


class MCPConnector:
    """Base for MCP-tool connectors.

    Class attributes (override in subclass):
        source_id:   registry id, e.g. ``"inven_mcp"``
        mcp_server:  MCP server name, e.g. ``"inven"``
        gate:        usually ``"shortlist_only"``
    """

    source_id: str = ""
    mcp_server: str = ""
    gate: str | None = "shortlist_only"

    def __init__(self, *, tool_caller: Any = None) -> None:
        # ``tool_caller`` is an injectable callable (server, tool, arguments) -> dict.
        # Stubbed until the MCP server is wired in a later step.
        self._tool_caller = tool_caller

    def _call_mcp_tool(self, server: str, tool: str, arguments: dict) -> dict:
        if self._tool_caller is None:
            raise NotImplementedError(
                f"{self.source_id}: MCP transport not yet wired. "
                "Inject a tool_caller or complete the MCP integration (plan §3.14)."
            )
        return self._tool_caller(server, tool, arguments)

    # ------------------------------------------------------------------
    # Contract — subclasses implement these
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:  # pragma: no cover - stub
        raise NotImplementedError

    def normalize(self, raw: RawRecord) -> CompanyRecord:  # pragma: no cover - stub
        raise NotImplementedError
