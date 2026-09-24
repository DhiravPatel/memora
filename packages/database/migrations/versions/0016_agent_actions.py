"""Agent actions: the approval gateway's record of an action, from request to done.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("request", JSONB, nullable=False, server_default="{}"),
        sa.Column("amount", sa.Float()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("check_id", sa.String(64)),
        sa.Column("approval_id", sa.String(64)),
        sa.Column("agent", sa.String(120)),
        sa.Column("api_key_id", sa.String(64)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("outcome_note", sa.Text()),
        sa.Column("external_ref", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_agent_actions_project_idempotency"),
    )
    op.create_index("ix_agent_actions_project_id", "agent_actions", ["project_id"])
    op.create_index(
        "ix_agent_actions_customer_action_created", "agent_actions", ["customer_id", "action", "created_at"]
    )
    op.create_index("ix_agent_actions_project_created", "agent_actions", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_actions_project_created", table_name="agent_actions")
    op.drop_index("ix_agent_actions_customer_action_created", table_name="agent_actions")
    op.drop_index("ix_agent_actions_project_id", table_name="agent_actions")
    op.drop_table("agent_actions")
