"""saved runs: runs.label + runs.conversation for revisitable chats.

Shared-workspace saved chats/lists (no per-user identity): a run gains an optional
user-facing ``label`` and a persisted ``conversation`` transcript so a completed
run can be listed, renamed, and re-opened with its chat intact.

Revision ID: 0003_saved_runs
Revises: 0002_run_orchestration
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_saved_runs"
down_revision = "0002_run_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("label", sa.String(), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "conversation", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "conversation")
    op.drop_column("runs", "label")
