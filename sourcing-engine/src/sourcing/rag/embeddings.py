"""EmbeddingProvider — `embed(texts) -> list[vector]` (plan §2).

Two implementations behind one contract:
  * ``HashingEmbeddingProvider`` — deterministic, dependency-free, offline.
    A hashing vectorizer over word tokens: cosine similarity reflects real
    lexical overlap, so it works as both the unit-test fake *and* a usable
    local default.
  * ``OllamaEmbeddingProvider`` — live embeddings via an Ollama embedding model.

Use :func:`get_embedding_provider` to construct the configured provider.
"""
from __future__ import annotations

import math
import re
from typing import Protocol

import httpx

from ..config import Settings, get_settings

Vector = list[float]
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[Vector]: ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _l2_normalize(vec: Vector) -> Vector:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class HashingEmbeddingProvider:
    """Deterministic hashing vectorizer (offline default + unit-test fake).

    Each token is hashed into one of ``dim`` buckets with a signed weight;
    the resulting vector is L2-normalised so dot product == cosine similarity.
    Fully deterministic and dependency-free.
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _hash(self, token: str) -> tuple[int, int]:
        h = 0
        for ch in token:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        bucket = h % self.dim
        sign = 1 if (h >> 1) & 1 else -1
        return bucket, sign

    def embed(self, texts: list[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in _tokenize(text):
                bucket, sign = self._hash(tok)
                vec[bucket] += sign
            out.append(_l2_normalize(vec))
        return out


class OllamaEmbeddingProvider:
    """Live embeddings via an Ollama embedding model (e.g. nomic-embed-text)."""

    def __init__(self, model: str, host: str, dim: int, timeout: float = 60.0):
        self.model = model
        self.host = host.rstrip("/")
        self.dim = dim
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[Vector]:
        out: list[Vector] = []
        with httpx.Client(timeout=self._timeout) as client:
            for text in texts:
                resp = client.post(
                    f"{self.host}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                out.append(resp.json()["embedding"])
        return out


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embed_provider == "ollama":
        return OllamaEmbeddingProvider(
            model=settings.embed_model,
            host=settings.ollama_host,
            dim=settings.embed_dim,
        )
    return HashingEmbeddingProvider(dim=settings.embed_dim)
