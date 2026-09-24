"""Memory evaluation: extraction cases, and runs measured under proposed settings.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_cases", sa.Column("kind", sa.String(16), nullable=False, server_default="retrieval"))
    op.add_column("eval_cases", sa.Column("event", JSONB))
    op.add_column("eval_cases", sa.Column("expectations", JSONB))
    op.add_column("eval_runs", sa.Column("overrides", JSONB))


def downgrade() -> None:
    op.drop_column("eval_runs", "overrides")
    # Extraction cases have no question to fall back on; they cannot outlive the columns.
    op.execute("DELETE FROM eval_cases WHERE kind <> 'retrieval'")
    op.drop_column("eval_cases", "expectations")
    op.drop_column("eval_cases", "event")
    op.drop_column("eval_cases", "kind")
