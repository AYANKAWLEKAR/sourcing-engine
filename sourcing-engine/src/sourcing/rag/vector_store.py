"""VectorStore — `upsert` / `query` (plan §2).

Two implementations behind one contract:
  * ``InMemoryVectorStore`` — deterministic, dependency-free (unit tests + CLI offline).
  * ``PgVectorStore`` — pgvector-backed (integration / prod).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

Vector = list[float]


@dataclass
class VectorHit:
    id: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _passes(meta: dict, filters: dict | None) -> bool:
    if not filters:
        return True
    return all(meta.get(k) == v for k, v in filters.items())


class VectorStore(Protocol):
    def upsert_many(self, items: list[tuple[str, Vector, dict]]) -> None: ...

    def query(
        self, vector: Vector, k: int, filters: dict | None = None
    ) -> list[VectorHit]: ...


class InMemoryVectorStore:
    """In-memory cosine-similarity store (deterministic)."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[Vector, dict]] = {}

    def upsert_many(self, items: list[tuple[str, Vector, dict]]) -> None:
        for id_, vec, meta in items:
            self._items[id_] = (list(vec), dict(meta))

    def upsert(self, id_: str, vector: Vector, meta: dict) -> None:
        self.upsert_many([(id_, vector, meta)])

    def query(
        self, vector: Vector, k: int, filters: dict | None = None
    ) -> list[VectorHit]:
        hits = [
            VectorHit(id=id_, score=cosine(vector, vec), meta=meta)
            for id_, (vec, meta) in self._items.items()
            if _passes(meta, filters)
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def __len__(self) -> int:
        return len(self._items)


class PgVectorStore:
    """pgvector-backed store over the ``source_embeddings`` table.

    Filtering is applied in Python after the ANN query for simplicity (the seed
    registry is small); the table holds the canonical vectors + metadata.
    """

    def __init__(self, session, dim: int):
        self.session = session
        self.dim = dim

    def upsert_many(self, items: list[tuple[str, Vector, dict]]) -> None:
        from sqlalchemy import text

        for id_, vec, meta in items:
            self.session.execute(
                text(
                    "INSERT INTO source_embeddings (source_id, embedding, meta) "
                    "VALUES (:sid, :emb, :meta) "
                    "ON CONFLICT (source_id) DO UPDATE SET embedding = :emb, meta = :meta"
                ),
                {"sid": id_, "emb": str(list(vec)), "meta": _json(meta)},
            )
        self.session.commit()

    def query(
        self, vector: Vector, k: int, filters: dict | None = None
    ) -> list[VectorHit]:
        from sqlalchemy import text

        rows = self.session.execute(
            text(
                "SELECT source_id, meta, 1 - (embedding <=> :vec) AS score "
                "FROM source_embeddings ORDER BY embedding <=> :vec LIMIT :k"
            ),
            {"vec": str(list(vector)), "k": k * 4},
        ).fetchall()
        hits = []
        for source_id, meta, score in rows:
            meta = meta or {}
            if _passes(meta, filters):
                hits.append(VectorHit(id=source_id, score=float(score), meta=meta))
        return hits[:k]


def _json(meta: dict) -> str:
    import json

    return json.dumps(meta)
