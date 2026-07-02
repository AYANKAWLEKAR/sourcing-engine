"""ORM tables for the Step-1 schema (plan §5.2).

Tables mirror the Pydantic models; JSON columns hold nested structs. The
``source_embeddings`` table carries a pgvector ``vector(EMBED_DIM)`` column.
"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..config import get_settings
from .base import Base

EMBED_DIM = get_settings().embed_dim


class RulesetRow(Base):
    __tablename__ = "rulesets"

    ruleset_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_version: Mapped[str] = mapped_column(String, nullable=False)
    thesis_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    ranking_weights: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FilterRuleRow(Base):
    __tablename__ = "filter_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruleset_id: Mapped[str] = mapped_column(
        ForeignKey("rulesets.ruleset_id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String, nullable=False)
    group: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    filter_type: Mapped[str] = mapped_column(String, nullable=False)
    screen_tier: Mapped[str] = mapped_column(String, nullable=False)
    logic: Mapped[dict] = mapped_column(JSON, default=dict)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    scrapeable: Mapped[bool] = mapped_column(Boolean, default=False)
    proxyable: Mapped[bool] = mapped_column(Boolean, default=False)
    discovery_action: Mapped[str] = mapped_column(String, nullable=False)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class Company(Base):
    """Declared in Step 1, populated later (spec §3.3)."""

    __tablename__ = "companies"

    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    abn: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    acn: Mapped[str | None] = mapped_column(String, nullable=True)
    legal_name: Mapped[str | None] = mapped_column(String, nullable=True)
    record: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SourceRegistryRow(Base):
    __tablename__ = "source_registry"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    connector_type: Mapped[str] = mapped_column(String, nullable=False)
    fields_provided: Mapped[list] = mapped_column(JSON, default=list)
    sectors_covered: Mapped[list] = mapped_column(JSON, default=list)
    geo_granularity: Mapped[str | None] = mapped_column(String, nullable=True)
    join_key: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_tier: Mapped[str] = mapped_column(String, default="free")
    freshness: Mapped[str | None] = mapped_column(String, nullable=True)
    reliability: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rate_limit: Mapped[str | None] = mapped_column(String, nullable=True)
    connector_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    capability_doc: Mapped[str] = mapped_column(String, default="")


class SourceEmbedding(Base):
    __tablename__ = "source_embeddings"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_registry.source_id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    ruleset_id: Mapped[str] = mapped_column(
        ForeignKey("rulesets.ruleset_id", ondelete="CASCADE"), index=True
    )
    source_plan: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String, default="schema")
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    pool_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    results_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
