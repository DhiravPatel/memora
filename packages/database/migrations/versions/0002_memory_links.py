"""Memory links: the causal and temporal graph between memories.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ID = sa.String(64)


def upgrade() -> None:
    op.create_table(
        "memory_links",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column("source_memory_id", ID, sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_memory_id", ID, sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_memory_id", "target_memory_id", "link_type", name="uq_memory_links_edge"
        ),
    )
    op.create_index("ix_memory_links_project_id", "memory_links", ["project_id"])
    op.create_index("ix_memory_links_customer_id", "memory_links", ["customer_id"])
    op.create_index("ix_memory_links_source_memory_id", "memory_links", ["source_memory_id"])
    op.create_index("ix_memory_links_target_memory_id", "memory_links", ["target_memory_id"])
    op.create_index("ix_memory_links_customer", "memory_links", ["customer_id", "link_type"])


def downgrade() -> None:
    op.drop_table("memory_links")
