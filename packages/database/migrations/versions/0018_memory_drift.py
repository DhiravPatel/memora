"""Memory drift: flags that a standing memory may be out of date (§26 5.5).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_drift",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_id", sa.String(64), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stated", sa.Text(), nullable=False),
        sa.Column("observed", sa.Text()),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(64)),
        sa.Column("resolved_by_type", sa.String(16)),
        sa.Column("note", sa.Text()),
        sa.Column("replacement_memory_id", sa.String(64)),
    )
    op.create_index("ix_memory_drift_memory_id", "memory_drift", ["memory_id"])
    op.create_index("ix_memory_drift_project_status", "memory_drift", ["project_id", "status", "detected_at"])
    op.create_index("ix_memory_drift_customer_status", "memory_drift", ["customer_id", "status"])
    op.create_index(
        "uq_memory_drift_open",
        "memory_drift",
        ["memory_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_memory_drift_open", table_name="memory_drift")
    op.drop_index("ix_memory_drift_customer_status", table_name="memory_drift")
    op.drop_index("ix_memory_drift_project_status", table_name="memory_drift")
    op.drop_index("ix_memory_drift_memory_id", table_name="memory_drift")
    op.drop_table("memory_drift")
