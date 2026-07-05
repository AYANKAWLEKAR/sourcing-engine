"""run orchestration: runs.status/state-machine columns + run_companies table.

Part C (next-phase plan §4.1): the run row becomes the observable state machine
(buybox → planning → acquiring → resolving → enriching → ranking → complete|failed)
and run_companies links a run to its candidate pool in the companies table.

Revision ID: 0002_run_orchestration
Revises: 0001_init
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_run_orchestration"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nothing reads the old `stage` column (RunRow was never written pre-Part C),
    # so the rename to the §4.1 vocabulary is free.
    op.alter_column("runs", "stage", new_column_name="status", server_default="buybox")
    op.alter_column("runs", "ruleset_id", existing_type=sa.String(), nullable=True)
    op.add_column(
        "runs",
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("runs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("shortlist", sa.JSON(), nullable=True))
    op.add_column(
        "runs",
        sa.Column("stage_history", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )

    op.create_table(
        "run_companies",
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity_id", sa.String(), primary_key=True),
        sa.Column("abn", sa.String(), nullable=True),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_run_companies_run_abn", "run_companies", ["run_id", "abn"])


def downgrade() -> None:
    op.drop_index("ix_run_companies_run_abn", table_name="run_companies")
    op.drop_table("run_companies")
    op.drop_column("runs", "stage_history")
    op.drop_column("runs", "shortlist")
    op.drop_column("runs", "error")
    op.drop_column("runs", "updated_at")
    op.alter_column("runs", "ruleset_id", existing_type=sa.String(), nullable=False)
    op.alter_column("runs", "status", new_column_name="stage", server_default="schema")
