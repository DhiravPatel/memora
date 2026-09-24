"""Customer snapshots and lifecycle states.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

No backfill, for the same reason as event outcomes (0010): a snapshot is a record of what
was known *at the time*, and the only honest way to produce one for a moment already past
is not to. The first processed event or nightly sweep after this ships takes each
customer's first snapshot and gives them their initial lifecycle state.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False, server_default="event"),
        sa.Column("event_id", sa.String(64)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("health_score", sa.Float()),
        sa.Column("health_band", sa.String(32)),
        sa.Column("state", sa.String(64)),
        sa.Column("plan", sa.String(64)),
        sa.Column("trajectory", sa.String(32)),
        sa.Column("open_problems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("churn_risk", sa.Float()),
        sa.Column("expansion_score", sa.Float()),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
        sa.Column("redacted_facts", JSONB),
        sa.Column("changes", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_customer_snapshots_project_id", "customer_snapshots", ["project_id"])
    op.create_index("ix_customer_snapshots_customer_taken", "customer_snapshots", ["customer_id", "taken_at"])
    op.create_index("ix_customer_snapshots_project_taken", "customer_snapshots", ["project_id", "taken_at"])

    op.create_table(
        "customer_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("previous_state", sa.String(64)),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("transition", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column("evaluation", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned_until", sa.DateTime(timezone=True)),
        sa.Column("actor_id", sa.String(64)),
        sa.Column("snapshot_id", sa.String(64)),
    )
    op.create_index("ix_customer_states_project_id", "customer_states", ["project_id"])
    op.create_index("ix_customer_states_customer_entered", "customer_states", ["customer_id", "entered_at"])
    op.create_index(
        "ix_customer_states_project_current",
        "customer_states",
        ["project_id", "state"],
        postgresql_where=sa.text("exited_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("customer_states")
    op.drop_table("customer_snapshots")
