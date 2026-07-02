"""Source connectors — the Protocol, the five base classes, and the loader.

Every concrete connector inherits from exactly one base class (which implements
the Protocol); none implement the Protocol directly. Pick the base class from the
registry entry's ``connector_type`` (plan §0).
"""
from .base_agent import AgentConnector
from .base_api import APIConnector
from .base_bulk import BulkConnector
from .base_mcp import MCPConnector
from .base_scrape import ScrapeConnector
from .loader import load_connector
from .protocol import RawRecord, SourceConnector

__all__ = [
    "RawRecord",
    "SourceConnector",
    "BulkConnector",
    "APIConnector",
    "ScrapeConnector",
    "AgentConnector",
    "MCPConnector",
    "load_connector",
]
