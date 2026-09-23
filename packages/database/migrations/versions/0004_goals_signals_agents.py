"""Goal tracking, signal snapshots and cross-session agent memory.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21

All four tables are additive: nothing existing changes shape, so the upgrade is safe to
run while the previous version is still serving traffic.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ID = sa.String(64)


def upgrade() -> None:
    # --- goals with a lifecycle -----------------------------------------------------
    op.create_table(
        "customer_goals",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_id", ID),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("keywords", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_reason", sa.String(255)),
        sa.Column("overridden_by", ID),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_goals_project_id", "customer_goals", ["project_id"])
    op.create_index("ix_customer_goals_customer_id", "customer_goals", ["customer_id"])
    op.create_index("ix_customer_goals_memory_id", "customer_goals", ["memory_id"])
    op.create_index("ix_customer_goals_status", "customer_goals", ["status"])
    op.create_index("ix_customer_goals_created_at", "customer_goals", ["created_at"])
    op.create_index("ix_customer_goals_customer_status", "customer_goals", ["customer_id", "status"])
    op.create_index("ix_customer_goals_project_status", "customer_goals", ["project_id", "status"])

    # --- daily signal snapshots -----------------------------------------------------
    op.create_table(
        "signal_snapshots",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("health_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("churn_risk", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expansion_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trajectory", sa.String(32), nullable=False, server_default="steady"),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signal_snapshots_project_id", "signal_snapshots", ["project_id"])
    op.create_index("ix_signal_snapshots_customer_id", "signal_snapshots", ["customer_id"])
    op.create_index(
        "ix_signal_snapshots_project_captured", "signal_snapshots", ["project_id", "captured_on"]
    )
    op.create_unique_constraint(
        "uq_signal_snapshots_customer_id_day", "signal_snapshots", ["customer_id", "captured_on"]
    )

    # --- agent sessions and turns ---------------------------------------------------
    op.create_table(
        "agent_sessions",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("agent", sa.String(120), nullable=False, server_default="agent"),
        sa.Column("channel", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text()),
        sa.Column("summary_memory_id", ID),
        sa.Column("memory_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_sessions_project_id", "agent_sessions", ["project_id"])
    op.create_index("ix_agent_sessions_customer_id", "agent_sessions", ["customer_id"])
    op.create_index("ix_agent_sessions_status", "agent_sessions", ["status"])
    op.create_index("ix_agent_sessions_last_active_at", "agent_sessions", ["last_active_at"])
    op.create_index("ix_agent_sessions_created_at", "agent_sessions", ["created_at"])
    op.create_index("ix_agent_sessions_customer_status", "agent_sessions", ["customer_id", "status"])
    op.create_unique_constraint(
        "uq_agent_sessions_project_id_external_id", "agent_sessions", ["project_id", "external_id"]
    )

    op.create_table(
        "agent_turns",
        sa.Column("id", ID, primary_key=True),
        sa.Column(
            "session_id", ID, sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", ID),
        sa.Column("retrieved_memory_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_agent_turns_session_id", "agent_turns", ["session_id"])
    op.create_index("ix_agent_turns_project_id", "agent_turns", ["project_id"])
    op.create_index("ix_agent_turns_session_occurred", "agent_turns", ["session_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("agent_turns")
    op.drop_table("agent_sessions")
    op.drop_table("signal_snapshots")
    op.drop_table("customer_goals")
