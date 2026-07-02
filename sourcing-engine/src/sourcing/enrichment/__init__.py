"""Enrichment layer: resolve scraped records to the ABN spine, merge fields."""
from .entity_resolution import EntityResolver

__all__ = ["EntityResolver"]
