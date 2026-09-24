"""Concept ids on memories, for paraphrase recall.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-24

Nullable, and filled by the worker rather than here: computing concepts is Python, and
rewriting every memory inside a migration would hold a long lock on the table a running
system reads from. ``backfill_concepts`` walks each project in batches; until it reaches a
memory, that memory is simply not a concept candidate — the other five strategies still
find it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("concepts", ARRAY(sa.String(48))))
    op.create_index("ix_memories_concepts", "memories", ["concepts"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_memories_concepts", table_name="memories")
    op.drop_column("memories", "concepts")
