"""Customer views: when a person or an agent key last looked at a customer (§26 4.1).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_views",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("viewer_type", sa.String(16), nullable=False),
        sa.Column("viewer_id", sa.String(64), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_viewed_at", sa.DateTime(timezone=True)),
        sa.Column("visits", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("customer_id", "viewer_type", "viewer_id", name="uq_customer_views_viewer"),
    )
    op.create_index("ix_customer_views_project_id", "customer_views", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_views_project_id", table_name="customer_views")
    op.drop_table("customer_views")
