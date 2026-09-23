"""Scoped API keys, outbound webhooks, team invitations, stored health.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ID = sa.String(64)


def upgrade() -> None:
    # --- scoped, revocable API keys ------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", ID),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", sa.String(64)),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_created_at", "api_keys", ["created_at"])
    op.create_index("ix_api_keys_project_active", "api_keys", ["project_id", "revoked_at"])

    # Every existing project keeps working: its key becomes a full-scope key row.
    op.execute(
        """
        INSERT INTO api_keys (id, project_id, name, key_hash, key_prefix, scopes, created_at, updated_at)
        SELECT
            'key_' || substr(md5(random()::text || p.id), 1, 20),
            p.id,
            'Default key',
            p.api_key_hash,
            p.api_key_prefix,
            '["admin"]'::jsonb,
            now(),
            now()
        FROM projects p
        """
    )

    # --- outbound webhooks ----------------------------------------------------------
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("secret", sa.String(128), nullable=False),
        sa.Column("event_types", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_webhook_endpoints_project_id", "webhook_endpoints", ["project_id"])
    op.create_index("ix_webhook_endpoints_created_at", "webhook_endpoints", ["created_at"])
    op.create_index(
        "ix_webhook_endpoints_project_active", "webhook_endpoints", ["project_id", "is_active"]
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint_id", ID, sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_id", ID, nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_deliveries_project_id", "webhook_deliveries", ["project_id"])
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_event_type", "webhook_deliveries", ["event_type"])
    op.create_index(
        "ix_webhook_deliveries_status_scheduled", "webhook_deliveries", ["status", "scheduled_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_project_created", "webhook_deliveries", ["project_id", "created_at"]
    )

    # --- team invitations -----------------------------------------------------------
    op.create_table(
        "user_invitations",
        sa.Column("id", ID, primary_key=True),
        sa.Column("organization_id", ID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("invited_by", ID),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_invitations_organization_id", "user_invitations", ["organization_id"])
    op.create_index("ix_user_invitations_email", "user_invitations", ["email"])
    op.create_index("ix_user_invitations_token_hash", "user_invitations", ["token_hash"], unique=True)
    op.create_index("ix_user_invitations_created_at", "user_invitations", ["created_at"])
    op.create_index("ix_user_invitations_org_status", "user_invitations", ["organization_id", "status"])

    # --- session invalidation + stored health + merge target -------------------------
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("customers", sa.Column("health_score", sa.Float()))
    op.add_column("customers", sa.Column("health_band", sa.String(32)))
    op.add_column("customers", sa.Column("health_computed_at", sa.DateTime(timezone=True)))
    op.add_column("customers", sa.Column("merged_into", ID))
    op.create_index("ix_customers_health_band", "customers", ["health_band"])
    op.create_index("ix_customers_merged_into", "customers", ["merged_into"])


def downgrade() -> None:
    op.drop_index("ix_customers_merged_into", table_name="customers")
    op.drop_index("ix_customers_health_band", table_name="customers")
    for column in ("merged_into", "health_computed_at", "health_band", "health_score"):
        op.drop_column("customers", column)
    op.drop_column("users", "token_version")
    op.drop_table("user_invitations")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
    op.drop_table("api_keys")
