"""Connectivity tests (plan §8.1). Require the local stack + Ollama.

Run with:  pytest -m integration
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sourcing.config import get_settings
from sourcing.llm import get_llm_client
from sourcing.rag.embeddings import get_embedding_provider
from sourcing.rag.registry_seed import load_seed_registry, upsert_registry_rows
from sourcing.rag.retriever import SPINE_SOURCES, SourceRetriever
from sourcing.rag.vector_store import PgVectorStore
from sourcing.ruleset.loader import load_origo_ruleset

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "rulesets",
    "filter_rules",
    "companies",
    "source_registry",
    "source_embeddings",
    "runs",
    "audit_log",
}


def test_db_connection(require_db, db_session):
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_pgvector_available(require_db, db_session):
    db_session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    present = db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    assert present == 1

    # Insert + cosine query a small vector to prove the operator works.
    dim = get_settings().embed_dim
    db_session.execute(text("DROP TABLE IF EXISTS _vec_probe"))
    db_session.execute(text(f"CREATE TEMP TABLE _vec_probe (id int, v vector({dim}))"))
    v = "[" + ",".join(["0.1"] * dim) + "]"
    db_session.execute(text("INSERT INTO _vec_probe VALUES (1, :v)"), {"v": v})
    dist = db_session.execute(
        text("SELECT v <=> :v FROM _vec_probe WHERE id = 1"), {"v": v}
    ).scalar()
    assert dist == pytest.approx(0.0, abs=1e-6)


def test_alembic_migrations(migrated_db, db_session):
    rows = db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).scalars()
    tables = set(rows)
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"

    # source_embeddings.embedding must be a vector column.
    udt = db_session.execute(
        text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'source_embeddings' AND column_name = 'embedding'"
        )
    ).scalar()
    assert udt == "vector"


def test_ollama_connectivity(require_ollama):
    settings = get_settings()
    llm = get_llm_client(settings)
    resp = llm.chat(
        model=settings.agent_model,
        system="You are a terse assistant.",
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
    )
    assert resp.text.strip(), "expected non-empty content from Ollama"


def test_embedding_provider_live(require_ollama):
    settings = get_settings()
    provider = get_embedding_provider(settings)
    vecs = provider.embed(["B2B testing and certification services"])
    assert len(vecs) == 1
    assert len(vecs[0]) == settings.embed_dim


def test_pgvector_retriever_roundtrip(require_db, migrated_db, db_session):
    settings = get_settings()
    registry = load_seed_registry()
    upsert_registry_rows(db_session, registry)  # FK: registry rows before embeddings

    store = PgVectorStore(db_session, dim=settings.embed_dim)
    embedder = get_embedding_provider(settings)
    retriever = SourceRetriever(store, embedder)
    retriever.index(registry)

    rs = load_origo_ruleset()
    rs.thesis_summary = "B2B testing inspection and certification services in QLD"
    plan = retriever.retrieve(rs, k=8)

    assert plan, "expected ranked hits from pgvector"
    assert {p.source_id for p in plan} & SPINE_SOURCES  # spine invariant holds live
    scores = [p.score for p in plan]
    assert scores == sorted(scores, reverse=True)  # ranked
