"""RAG source retrieval."""
from .embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    OllamaEmbeddingProvider,
    get_embedding_provider,
)
from .registry_seed import load_seed_registry
from .retriever import SourceRetriever, build_query_text, required_fields
from .vector_store import InMemoryVectorStore, PgVectorStore, VectorHit, VectorStore

__all__ = [
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "get_embedding_provider",
    "load_seed_registry",
    "SourceRetriever",
    "build_query_text",
    "required_fields",
    "InMemoryVectorStore",
    "PgVectorStore",
    "VectorHit",
    "VectorStore",
]
