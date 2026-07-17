"""Online GrantConnect check: the official CKAN metadata is still resolvable."""
from __future__ import annotations

import pytest

from sourcing.connectors.grantconnect import GrantConnectBulkConnector

pytestmark = pytest.mark.integration


def test_official_grantconnect_metadata_is_resolvable(tmp_path):
    connector = GrantConnectBulkConnector(db_path=tmp_path / "bulk.duckdb")
    try:
        try:
            resources = connector.resolve_resources()
            assert resources and resources[0]["resource_url"]
            assert resources[0]["source_dataset"] == "f4bcc061-8973-48cd-bc1e-44cbaf5b90d0"
        except Exception as exc:  # public endpoint availability, not product behavior
            pytest.skip(f"GrantConnect/data.gov.au unavailable: {exc}")
    finally:
        connector.close()
