"""A curated glossary alongside the mined vocabulary.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23

Adds the columns that let a person teach the project a pair directly, or throw a mined one
out for good. Existing rows are mined and active, which is what the defaults say, so the
upgrade needs no backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ID = sa.String(64)


def upgrade() -> None:
    op.add_column(
        "learned_terms",
        sa.Column("source", sa.String(16), nullable=False, server_default="mined"),
    )
    op.add_column(
        "learned_terms",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column("learned_terms", sa.Column("decided_by", ID))
    op.add_column("learned_terms", sa.Column("note", sa.String(255)))
    op.create_index("ix_learned_terms_source", "learned_terms", ["source"])


def downgrade() -> None:
    op.drop_index("ix_learned_terms_source", table_name="learned_terms")
    op.drop_column("learned_terms", "note")
    op.drop_column("learned_terms", "decided_by")
    op.drop_column("learned_terms", "status")
    op.drop_column("learned_terms", "source")
