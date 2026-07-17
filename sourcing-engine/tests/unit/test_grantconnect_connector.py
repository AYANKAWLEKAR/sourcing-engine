"""Offline coverage for GrantConnect CSV ingestion and enrichment."""
from __future__ import annotations

from datetime import date

import pytest

from sourcing.config import DATA_DIR
from sourcing.connectors.base_bulk import BulkConnector
from sourcing.connectors.grantconnect import GrantConnectBulkConnector
from sourcing.models.company import CompanyRecord


def _connector(tmp_path) -> GrantConnectBulkConnector:
    awards = tmp_path / "awards.csv"
    awards.write_text(
        "Recipient Name,Recipient ABN,Grant Program,Grant Award Value,Grant Award Date,Recipient State,Grant Purpose\n"
        "Acme Manufacturing Pty Ltd,12 345 678 901,Modern Manufacturing,\"$1,250\",15/06/2025,QLD,Robotics line\n"
        "Acme Manufacturing Pty Ltd,12345678901,Export Market Development,750000.50,2024-02-01,QLD,Export\n"
        "Acme Manufacturing Pty Ltd,12345678901,Modern Manufacturing,\"$1,250\",15/06/2025,QLD,Robotics line\n"
        "Invalid Recipient,not-an-abn,Test,50,2024-01-01,NSW,Ignore\n",
        encoding="latin-1",
    )
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "  - source_dataset: fixture-industry\n"
        "    granting_agency: Department of Industry, Science and Resources\n"
        "    file: awards.csv\n",
        encoding="utf-8",
    )
    connector = GrantConnectBulkConnector(
        db_path=tmp_path / "bulk.duckdb", sources_path=config, raw_dir=tmp_path / "raw"
    )
    connector.ensure_loaded()
    return connector


def test_inherits_bulk_connector(tmp_path):
    connector = _connector(tmp_path)
    try:
        assert isinstance(connector, BulkConnector)
        assert connector.row_count() == 2  # exact duplicate and invalid ABN were removed
    finally:
        connector.close()


def test_default_source_requires_explicit_manual_staging(tmp_path):
    connector = GrantConnectBulkConnector(
        db_path=tmp_path / "bulk.duckdb", sources_path=DATA_DIR / "grantconnect_sources.yaml"
    )
    try:
        with pytest.raises(RuntimeError, match="manual staging"):
            connector.ensure_loaded()
    finally:
        connector.close()


def test_fetch_normalizes_abn_and_keeps_award_history(tmp_path):
    connector = _connector(tmp_path)
    try:
        rows = connector.fetch({"abn": "12 345 678 901"})
        assert len(rows) == 2
        assert [r["program_name"] for r in rows] == ["Modern Manufacturing", "Export Market Development"]
        assert connector.fetch({"abn": "bad"}) == []
    finally:
        connector.close()


def test_enrich_record_aggregates_distinct_grant_signal(tmp_path):
    connector = _connector(tmp_path)
    try:
        record = CompanyRecord(abn="12345678901", legal_name="Acme Manufacturing Pty Ltd")
        connector.enrich_record(record)
        m = record.moat_signals
        assert m.gov_investment is True
        assert m.gov_grants_count == 2
        assert m.gov_grants_total_aud == 751_250  # model stores whole AUD, truncating 50 cents
        assert m.gov_grant_programs == ["Export Market Development", "Modern Manufacturing"]
        assert m.gov_grants_most_recent == date(2025, 6, 15)
        assert m.gov_contracts is False  # grants never masquerade as procurement
        assert any(p.source == "grantconnect_awards" for p in record.provenance)
    finally:
        connector.close()


def test_normalize_one_award_creates_company_fragment(tmp_path):
    connector = _connector(tmp_path)
    try:
        fragment = connector.normalize(connector.fetch({"abn": "12345678901"})[0])
        assert fragment.moat_signals.gov_investment is True
        assert fragment.moat_signals.gov_grants_count == 1
        assert fragment.moat_signals.gov_grants_total_aud == 1_250
    finally:
        connector.close()
