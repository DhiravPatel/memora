"""Event contracts, and the contract check on each event (§26 7.1).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_contracts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="warn"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_rejected_at", sa.DateTime(timezone=True)),
        sa.Column("recent_rejections", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_reported_at", sa.DateTime(timezone=True)),
        sa.Column("reported_rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "event_type", name="uq_event_contracts_type"),
    )
    op.create_index("ix_event_contracts_project_id", "event_contracts", ["project_id"])
    op.create_index("ix_event_contracts_created_at", "event_contracts", ["created_at"])
    op.add_column("events", sa.Column("contract", postgresql.JSONB()))
    # Violation reports read one project's events of one type over a window.
    op.create_index("ix_events_project_type_created", "events", ["project_id", "event_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_events_project_type_created", table_name="events")
    op.drop_column("events", "contract")
    op.drop_index("ix_event_contracts_created_at", table_name="event_contracts")
    op.drop_index("ix_event_contracts_project_id", table_name="event_contracts")
    op.drop_table("event_contracts")
