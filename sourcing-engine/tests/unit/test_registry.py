"""Registry/loader tests (plan §5).

Verifies that every *built* connector in the registry resolves via ``load_connector``
to an instance of the base class implied by its ``connector_type`` — the single
test that catches the "wrong base class" bug (e.g. a live API put on BulkConnector).

Connectors not yet built (no ``connector_built: true``) are skipped: their
``connector_ref`` is registry metadata for the RAG retriever until they're
implemented in a later pass.
"""
from __future__ import annotations

import yaml

from sourcing.config import DATA_DIR
from sourcing.connectors import (
    AgentConnector,
    APIConnector,
    BulkConnector,
    MCPConnector,
    ScrapeConnector,
    load_connector,
)

_TYPE_TO_BASE = {
    "bulk": BulkConnector,
    "api": APIConnector,
    "scrape": ScrapeConnector,
    "agent": AgentConnector,
    "mcp": MCPConnector,
}

# Extra constructor kwargs for connectors that need them at instantiation.
# Paths are synthetic: constructors never touch disk (validated at ensure_loaded).
_KWARGS = {
    "abn_lookup_api": {"guid": "test-guid"},
    "asic_company_dataset": {"csv_path": "x"},
    "abn_bulk_extract": {"zip_paths": ["x.zip"], "allow_download": False},
    "ipgod": {"csv_paths": ["x.csv"]},
    "asx_listed_list": {"csv_path": "x.csv"},
}


def _registry_entries() -> list[dict]:
    raw = yaml.safe_load((DATA_DIR / "source_registry.yaml").read_text())
    return raw["sources"]


def test_built_connectors_resolve_to_correct_base_class():
    built = [e for e in _registry_entries() if e.get("connector_built")]
    assert built, "expected at least one built connector in the registry"

    for entry in built:
        ref = entry["connector_ref"]
        base = _TYPE_TO_BASE[entry["connector_type"]]
        kwargs = _KWARGS.get(entry["source_id"], {})
        obj = load_connector(ref, **kwargs)
        assert isinstance(obj, base), (
            f"{entry['source_id']}: {ref} is not a {base.__name__} "
            f"(connector_type={entry['connector_type']})"
        )
        assert obj.source_id == entry["source_id"]


def test_spine_and_text_sources_present_and_enabled():
    entries = _registry_entries()
    enabled = {e["source_id"] for e in entries if e.get("enabled")}

    spine = {"abn_bulk_extract", "abn_lookup_api", "asic_company_dataset"}
    text = {"google_maps", "yellow_pages", "industrynet", "website_fetch"}

    assert enabled & spine, "no spine source enabled"
    assert enabled & text, "no text/category source enabled"


def test_disabled_sources_are_flagged():
    entries = _registry_entries()
    # At least one source is intentionally disabled (e.g. linkedin) — guards the
    # enabled flag actually round-trips through the registry.
    assert any(e.get("enabled") is False for e in entries)
