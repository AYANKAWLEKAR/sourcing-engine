"""init schema: rulesets, filter_rules, companies, source_registry,
source_embeddings (pgvector), runs, audit_log.

Revision ID: 0001_init
Revises:
Create Date: 2026-06-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from sourcing.config import get_settings

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

EMBED_DIM = get_settings().embed_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "rulesets",
        sa.Column("ruleset_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_version", sa.String(), nullable=False),
        sa.Column("thesis_summary", sa.String(), nullable=True),
        sa.Column("ranking_weights", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "filter_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ruleset_id",
            sa.String(),
            sa.ForeignKey("rulesets.ruleset_id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("group", sa.String(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("filter_type", sa.String(), nullable=False),
        sa.Column("screen_tier", sa.String(), nullable=False),
        sa.Column("logic", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("scrapeable", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("proxyable", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("discovery_action", sa.String(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
    )

    op.create_table(
        "companies",
        sa.Column("entity_id", sa.String(), primary_key=True),
        sa.Column("abn", sa.String(), index=True, nullable=True),
        sa.Column("acn", sa.String(), nullable=True),
        sa.Column("legal_name", sa.String(), nullable=True),
        sa.Column("record", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "source_registry",
        sa.Column("source_id", sa.String(), primary_key=True),
        sa.Column("connector_type", sa.String(), nullable=False),
        sa.Column("fields_provided", sa.JSON(), nullable=True),
        sa.Column("sectors_covered", sa.JSON(), nullable=True),
        sa.Column("geo_granularity", sa.String(), nullable=True),
        sa.Column("join_key", sa.String(), nullable=True),
        sa.Column("cost_tier", sa.String(), server_default="free"),
        sa.Column("freshness", sa.String(), nullable=True),
        sa.Column("reliability", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("rate_limit", sa.String(), nullable=True),
        sa.Column("connector_ref", sa.String(), nullable=True),
        sa.Column("capability_doc", sa.String(), server_default=""),
    )

    op.create_table(
        "source_embeddings",
        sa.Column(
            "source_id",
            sa.String(),
            sa.ForeignKey("source_registry.source_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding", Vector(EMBED_DIM)),
        sa.Column("meta", sa.JSON(), nullable=True),
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column(
            "ruleset_id",
            sa.String(),
            sa.ForeignKey("rulesets.ruleset_id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("source_plan", sa.JSON(), nullable=True),
        sa.Column("stage", sa.String(), server_default="schema"),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("pool_ref", sa.String(), nullable=True),
        sa.Column("results_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("runs")
    op.drop_table("source_embeddings")
    op.drop_table("source_registry")
    op.drop_table("companies")
    op.drop_table("filter_rules")
    op.drop_table("rulesets")
