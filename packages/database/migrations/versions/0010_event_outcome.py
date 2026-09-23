"""Why an event did or did not become a memory.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23

Nullable and not backfilled. An event processed before this shipped has no record of the
decisions that were made about it — those decisions were never written down, and inventing
them now by re-running the pipeline against today's settings and today's memories would
produce a confident answer to the wrong question. Re-processing an old event fills it in
honestly; until then, null means "we do not know", which is true.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("outcome", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("events", "outcome")
