"""Retrieval evaluation sets, cases and runs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_sets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_eval_sets_project_name"),
    )
    op.create_index("ix_eval_sets_project_id", "eval_sets", ["project_id"])
    op.create_index("ix_eval_sets_created_at", "eval_sets", ["created_at"])

    op.create_table(
        "eval_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("set_id", sa.String(64), sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_memory_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("expected_phrases", JSONB, nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text()),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_eval_cases_project_id", "eval_cases", ["project_id"])
    op.create_index("ix_eval_cases_set", "eval_cases", ["set_id", "created_at"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("set_id", sa.String(64), sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("label", sa.String(120)),
        sa.Column("cleared", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("k", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("results", JSONB, nullable=False, server_default="[]"),
        sa.Column("comparison", JSONB),
        sa.Column("baseline_run_id", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_eval_runs_project_id", "eval_runs", ["project_id"])
    op.create_index("ix_eval_runs_set_created", "eval_runs", ["set_id", "created_at"])


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_table("eval_sets")
