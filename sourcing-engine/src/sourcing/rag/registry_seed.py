"""Load the Source Registry seed from YAML into SourceRegistryEntry objects."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import DATA_DIR
from ..models.source import SourceRegistryEntry


def load_seed_registry(path: Path | None = None) -> list[SourceRegistryEntry]:
    path = path or (DATA_DIR / "source_registry.yaml")
    raw = yaml.safe_load(Path(path).read_text())
    entries = [SourceRegistryEntry(**item) for item in raw["sources"]]
    return entries


def upsert_registry_rows(session, entries: list[SourceRegistryEntry]) -> None:
    """Upsert seed entries into the ``source_registry`` table.

    The ``source_embeddings`` table references ``source_registry`` by FK, so the
    registry rows must exist before embeddings are indexed.
    """
    from sqlalchemy import text

    for e in entries:
        session.execute(
            text(
                "INSERT INTO source_registry "
                "(source_id, connector_type, fields_provided, sectors_covered, "
                " geo_granularity, join_key, cost_tier, freshness, reliability, "
                " enabled, rate_limit, connector_ref, capability_doc) "
                "VALUES (:source_id, :connector_type, :fields_provided, :sectors_covered, "
                " :geo_granularity, :join_key, :cost_tier, :freshness, :reliability, "
                " :enabled, :rate_limit, :connector_ref, :capability_doc) "
                "ON CONFLICT (source_id) DO UPDATE SET "
                " connector_type = EXCLUDED.connector_type, "
                " fields_provided = EXCLUDED.fields_provided, "
                " enabled = EXCLUDED.enabled, "
                " capability_doc = EXCLUDED.capability_doc"
            ),
            {
                "source_id": e.source_id,
                "connector_type": e.connector_type.value,
                "fields_provided": _json(e.fields_provided),
                "sectors_covered": _json(e.sectors_covered),
                "geo_granularity": e.geo_granularity,
                "join_key": e.join_key,
                "cost_tier": e.cost_tier.value,
                "freshness": e.freshness,
                "reliability": e.reliability,
                "enabled": e.enabled,
                "rate_limit": e.rate_limit,
                "connector_ref": e.connector_ref,
                "capability_doc": e.capability_doc,
            },
        )
    session.commit()


def _json(value) -> str:
    import json

    return json.dumps(value)
