"""When each encryption key came into use.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

The active key is injected from a KMS and has no creation date the application can read,
so the nightly rotation check has nothing to measure against until something records one.
This table is that record: a key id and the first time a process saw it.

No backfill. A deployment that has been running for a year will see its key "first used"
on the day this ships, which is wrong by up to a year and is the only honest answer
available — the alternative is inventing a date. The first check after a real rotation is
accurate, and that is the one that matters.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encryption_key_uses",
        sa.Column("key_id", sa.String(32), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("encryption_key_uses")
