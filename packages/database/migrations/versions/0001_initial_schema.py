"""Initial schema: organizations, projects, customers, events, memories, graph, vectors.

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from common.settings import get_settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ID = sa.String(64)
EMBEDDING_DIMENSIONS = get_settings().embedding_dimensions


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "organizations",
        sa.Column("id", ID, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_organizations_created_at", "organizations", ["created_at"])

    op.create_table(
        "users",
        sa.Column("id", ID, primary_key=True),
        sa.Column("organization_id", ID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("role", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    op.create_table(
        "projects",
        sa.Column("id", ID, primary_key=True),
        sa.Column("organization_id", ID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(128), nullable=False),
        sa.Column("api_key_prefix", sa.String(32), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_projects_organization_id_name"),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])
    op.create_index("ix_projects_api_key_hash", "projects", ["api_key_hash"], unique=True)
    op.create_index("ix_projects_created_at", "projects", ["created_at"])

    op.create_table(
        "customers",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("name", sa.String(255)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "external_id", name="uq_customers_project_id_external_id"),
    )
    op.create_index("ix_customers_project_id", "customers", ["project_id"])
    op.create_index("ix_customers_email", "customers", ["email"])
    op.create_index("ix_customers_created_at", "customers", ["created_at"])

    op.create_table(
        "events",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("external_event_id", sa.String(255)),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(64), nullable=False, server_default="api"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("project_id", "external_event_id", name="uq_events_project_id_external_event_id"),
    )
    op.create_index("ix_events_project_id", "events", ["project_id"])
    op.create_index("ix_events_customer_id", "events", ["customer_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_customer_occurred_at", "events", ["customer_id", "occurred_at"])
    op.create_index("ix_events_project_status", "events", ["project_id", "status"])

    op.create_table(
        "memories",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("source", sa.String(32), nullable=False, server_default="event"),
        sa.Column("source_event_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by", ID),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memories_project_id", "memories", ["project_id"])
    op.create_index("ix_memories_customer_id", "memories", ["customer_id"])
    op.create_index("ix_memories_content_hash", "memories", ["content_hash"])
    op.create_index("ix_memories_last_seen_at", "memories", ["last_seen_at"])
    op.create_index("ix_memories_expires_at", "memories", ["expires_at"])
    op.create_index("ix_memories_created_at", "memories", ["created_at"])
    op.create_index("ix_memories_customer_status", "memories", ["customer_id", "status"])
    op.create_index("ix_memories_project_type", "memories", ["project_id", "type"])
    op.create_index("ix_memories_customer_importance", "memories", ["customer_id", "importance"])
    # Full-text index backing the keyword leg of hybrid retrieval.
    op.execute(
        "CREATE INDEX ix_memories_content_fts ON memories "
        "USING gin (to_tsvector('english', content))"
    )

    op.create_table(
        "memory_versions",
        sa.Column("id", ID, primary_key=True),
        sa.Column("memory_id", ID, sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_content", sa.Text()),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("previous_importance", sa.Float()),
        sa.Column("new_importance", sa.Float()),
        sa.Column("previous_confidence", sa.Float()),
        sa.Column("new_confidence", sa.Float()),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("source_event_id", ID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_versions_memory_id", "memory_versions", ["memory_id"])
    op.create_index("ix_memory_versions_memory_created", "memory_versions", ["memory_id", "created_at"])

    op.create_table(
        "entities",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "type", "normalized_name", name="uq_entities_project_type_name"),
    )
    op.create_index("ix_entities_project_id", "entities", ["project_id"])
    op.create_index("ix_entities_normalized_name", "entities", ["normalized_name"])
    op.create_index("ix_entities_created_at", "entities", ["created_at"])

    op.create_table(
        "relationships",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_entity_id", ID, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("target_entity_id", ID, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "source_entity_id", "relationship_type", "target_entity_id",
            name="uq_relationships_edge",
        ),
    )
    op.create_index("ix_relationships_project_id", "relationships", ["project_id"])
    op.create_index("ix_relationships_source_entity_id", "relationships", ["source_entity_id"])
    op.create_index("ix_relationships_target_entity_id", "relationships", ["target_entity_id"])
    op.create_index("ix_relationships_created_at", "relationships", ["created_at"])

    op.create_table(
        "memory_entities",
        sa.Column("id", ID, primary_key=True),
        sa.Column("memory_id", ID, sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", ID, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("memory_id", "entity_id", name="uq_memory_entities_memory_id_entity_id"),
    )
    op.create_index("ix_memory_entities_memory_id", "memory_entities", ["memory_id"])
    op.create_index("ix_memory_entities_entity_id", "memory_entities", ["entity_id"])

    op.create_table(
        "embeddings",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_id", ID, sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("memory_id", "model", name="uq_embeddings_memory_id_model"),
    )
    op.create_index("ix_embeddings_project_id", "embeddings", ["project_id"])
    op.create_index("ix_embeddings_memory_id", "embeddings", ["memory_id"])
    # HNSW gives good recall/latency without tuning list counts as data grows.
    op.execute(
        "CREATE INDEX ix_embeddings_vector ON embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", ID, primary_key=True),
        sa.Column("organization_id", ID),
        sa.Column("project_id", ID),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", ID),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("resource_id", ID),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_org_created", "audit_logs", ["organization_id", "created_at"])

    op.create_table(
        "usage_records",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "day", "metric", name="uq_usage_records_project_day_metric"),
    )
    op.create_index("ix_usage_records_project_id", "usage_records", ["project_id"])
    op.create_index("ix_usage_records_day", "usage_records", ["day"])

    op.create_table(
        "query_logs",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", ID),
        sa.Column("kind", sa.String(32), nullable=False, server_default="query"),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("memory_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("event_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_query_logs_project_id", "query_logs", ["project_id"])
    op.create_index("ix_query_logs_customer_id", "query_logs", ["customer_id"])
    op.create_index("ix_query_logs_project_created", "query_logs", ["project_id", "created_at"])


def downgrade() -> None:
    for table in (
        "query_logs",
        "usage_records",
        "audit_logs",
        "embeddings",
        "memory_entities",
        "relationships",
        "entities",
        "memory_versions",
        "memories",
        "events",
        "customers",
        "projects",
        "users",
        "organizations",
    ):
        op.drop_table(table)
