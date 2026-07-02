"""Integration fixtures — live Postgres/pgvector + Ollama. All marked `integration`."""
from __future__ import annotations

import subprocess

import httpx
import pytest
from sqlalchemy import text

from sourcing.config import REPO_ROOT, get_settings
from sourcing.db import get_engine, session_scope

pytestmark = pytest.mark.integration


def _db_reachable() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _ollama_reachable() -> bool:
    s = get_settings()
    try:
        httpx.get(f"{s.ollama_host.rstrip('/')}/api/tags", timeout=3.0).raise_for_status()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def require_db():
    if not _db_reachable():
        pytest.skip("Postgres not reachable (run `docker compose up -d`).")


@pytest.fixture(scope="session")
def require_ollama():
    if not _ollama_reachable():
        pytest.skip("Ollama not reachable (run `ollama serve`).")


@pytest.fixture(scope="session")
def migrated_db(require_db):
    """Apply Alembic migrations to head (idempotent)."""
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return True


@pytest.fixture
def db_session(migrated_db):
    with session_scope() as s:
        yield s


@pytest.fixture(scope="session")
def require_abn_guid():
    """Skip integration tests that need a live ABN Lookup GUID."""
    s = get_settings()
    if not s.abn_lookup_guid:
        pytest.skip("ABN_LOOKUP_GUID not set in .env (required for ABN integration tests).")
    return s.abn_lookup_guid


@pytest.fixture(scope="session")
def require_asic_csv():
    """Skip integration tests that need the local ASIC CSV extract."""
    from pathlib import Path

    s = get_settings()
    if not s.asic_csv_path or not Path(s.asic_csv_path).exists():
        pytest.skip("ASIC_CSV_PATH not set / file missing (required for ASIC integration tests).")
    return s.asic_csv_path


@pytest.fixture(scope="session")
def require_apify_token():
    """Skip integration tests that run live Apify actors."""
    s = get_settings()
    if not s.apify_api_token:
        pytest.skip("APIFY_API_TOKEN not set in .env (required for live scrape tests).")
    return s.apify_api_token
