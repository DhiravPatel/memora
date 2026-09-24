"""Lifecycle tracks: several state machines per customer.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing stay belongs to the primary track, which is what it always was.
    op.add_column(
        "customer_states",
        sa.Column("track", sa.String(40), nullable=False, server_default="lifecycle"),
    )
    op.drop_index("ix_customer_states_project_current", table_name="customer_states")
    op.create_index(
        "ix_customer_states_project_current",
        "customer_states",
        ["project_id", "track", "state"],
        postgresql_where=sa.text("exited_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_customer_states_project_current", table_name="customer_states")
    # Extra tracks cannot live in a one-track table; keep only the primary's history.
    op.execute("DELETE FROM customer_states WHERE track <> 'lifecycle'")
    op.drop_column("customer_states", "track")
    op.create_index(
        "ix_customer_states_project_current",
        "customer_states",
        ["project_id", "state"],
        postgresql_where=sa.text("exited_at IS NULL"),
    )
