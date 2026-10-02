"""Customer personalizations: what a product should do differently for a customer (§26 6.6).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_personalizations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("settings_hash", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False, server_default="event"),
        sa.UniqueConstraint("customer_id", name="uq_customer_personalizations_customer"),
    )
    op.create_index("ix_customer_personalizations_project", "customer_personalizations", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_personalizations_project", table_name="customer_personalizations")
    op.drop_table("customer_personalizations")
