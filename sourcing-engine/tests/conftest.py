"""Shared fixtures (plan §8.3)."""
from __future__ import annotations

import pytest

from sourcing.config import get_settings
from sourcing.models.filter_rule import FilterRuleset
from sourcing.rag.embeddings import HashingEmbeddingProvider
from sourcing.rag.registry_seed import load_seed_registry
from sourcing.rag.vector_store import InMemoryVectorStore
from sourcing.ruleset.loader import load_origo_ruleset


@pytest.fixture(scope="session")
def base_ruleset() -> FilterRuleset:
    """The Origo CSV loaded once per session."""
    return load_origo_ruleset()


@pytest.fixture
def fresh_ruleset() -> FilterRuleset:
    """A deep-copyable fresh load for tests that mutate the ruleset."""
    return load_origo_ruleset()


@pytest.fixture(scope="session")
def seed_registry():
    return load_seed_registry()


@pytest.fixture
def fake_embedder() -> HashingEmbeddingProvider:
    return HashingEmbeddingProvider(dim=get_settings().embed_dim)


@pytest.fixture
def fake_vector_store() -> InMemoryVectorStore:
    return InMemoryVectorStore()
