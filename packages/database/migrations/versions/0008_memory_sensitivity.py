"""Per-memory sensitivity, for the project's restriction policy.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

Every existing memory is ``normal``, which is what the default says, so no backfill is
needed. Turning a policy on classifies memories written *after* it — existing ones are
reclassified by ``reclassify_memories``, which the settings write enqueues, because
rewriting a million rows inside a migration is not a thing to do to a running system.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("sensitivity", sa.String(16), nullable=False, server_default="normal"),
    )
    op.create_index("ix_memories_sensitivity", "memories", ["sensitivity"])


def downgrade() -> None:
    op.drop_index("ix_memories_sensitivity", table_name="memories")
    op.drop_column("memories", "sensitivity")
