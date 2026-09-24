"""Agent profiles, guardrail checks, approvals, and query logs as agent runs.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("readable_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("can_read_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_actions", JSONB, nullable=False, server_default="[]"),
        sa.Column("denied_actions", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_agent_profiles_project_name"),
    )
    op.create_index("ix_agent_profiles_project_id", "agent_profiles", ["project_id"])
    op.create_index("ix_agent_profiles_created_at", "agent_profiles", ["created_at"])

    op.add_column(
        "api_keys",
        sa.Column("agent_profile_id", sa.String(64), sa.ForeignKey("agent_profiles.id", ondelete="SET NULL")),
    )

    op.create_table(
        "agent_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent", sa.String(120)),
        sa.Column("api_key_id", sa.String(64)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("request", JSONB, nullable=False, server_default="{}"),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("approval_id", sa.String(64)),
        sa.Column("snapshot_id", sa.String(64)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_checks_project_created", "agent_checks", ["project_id", "created_at"])
    op.create_index("ix_agent_checks_customer_created", "agent_checks", ["customer_id", "created_at"])

    op.create_table(
        "agent_approvals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(120)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("request", JSONB, nullable=False, server_default="{}"),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text()),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_approvals_project_status", "agent_approvals", ["project_id", "status", "created_at"])

    # Query logs become agent runs: who asked, the customer's state then, and the trace.
    op.add_column("query_logs", sa.Column("api_key_id", sa.String(64)))
    op.add_column("query_logs", sa.Column("agent", sa.String(120)))
    op.add_column("query_logs", sa.Column("session_id", sa.String(64)))
    op.add_column("query_logs", sa.Column("snapshot_id", sa.String(64)))
    op.add_column("query_logs", sa.Column("cleared", sa.Boolean()))
    op.add_column("query_logs", sa.Column("trace", JSONB))
    op.create_index("ix_query_logs_session", "query_logs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_query_logs_session", table_name="query_logs")
    for column in ("trace", "cleared", "snapshot_id", "session_id", "agent", "api_key_id"):
        op.drop_column("query_logs", column)
    op.drop_table("agent_approvals")
    op.drop_table("agent_checks")
    op.drop_column("api_keys", "agent_profile_id")
    op.drop_table("agent_profiles")
