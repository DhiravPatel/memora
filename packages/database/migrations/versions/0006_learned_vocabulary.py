"""A project's mined vocabulary.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21

Additive. The table is empty until the nightly mining job runs, and an empty table simply
means retrieval uses the shipped synonym list on its own, exactly as before.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ID = sa.String(64)


def upgrade() -> None:
    op.create_table(
        "learned_terms",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term", sa.String(64), nullable=False),
        sa.Column("synonym", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("support", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_learned_terms_project_id", "learned_terms", ["project_id"])
    op.create_index("ix_learned_terms_project_score", "learned_terms", ["project_id", "score"])
    op.create_unique_constraint(
        "uq_learned_terms_project_pair", "learned_terms", ["project_id", "term", "synonym"]
    )


def downgrade() -> None:
    op.drop_table("learned_terms")
