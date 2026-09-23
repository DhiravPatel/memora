"""Schema coherence checks that do not need a live database.

These compile the ORM metadata against the PostgreSQL dialect (catching bad types,
constraints and defaults) and verify the initial migration covers every table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from database import models  # noqa: F401  (registers the tables)
from database.base import Base

MIGRATION = Path("packages/database/migrations/versions/0001_initial_schema.py")

EXPECTED_TABLES = {
    "organizations",
    "users",
    "projects",
    "customers",
    "events",
    "memories",
    "memory_versions",
    "memory_entities",
    "entities",
    "relationships",
    "embeddings",
    "audit_logs",
    "usage_records",
    "query_logs",
}


def test_every_expected_table_is_mapped():
    assert set(Base.metadata.tables) >= EXPECTED_TABLES


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_tables_compile_for_postgres(table_name: str):
    ddl = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=postgresql.dialect()))
    assert f'CREATE TABLE {table_name}' in ddl


def test_initial_migration_creates_every_table():
    source = MIGRATION.read_text()
    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert created >= EXPECTED_TABLES, EXPECTED_TABLES - created


def test_migration_enables_pgvector_and_its_indexes():
    source = MIGRATION.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in source
    assert "hnsw (embedding vector_cosine_ops)" in source
    assert "gin (to_tsvector('english', content))" in source


def test_tenant_scoped_tables_carry_project_id():
    for table_name in ("customers", "events", "memories", "entities", "relationships", "embeddings"):
        assert "project_id" in Base.metadata.tables[table_name].columns


def test_idempotency_and_uniqueness_constraints_exist():
    constraint_names = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
    }
    assert "uq_events_project_id_external_event_id" in constraint_names
    assert "uq_customers_project_id_external_id" in constraint_names
    assert "uq_entities_project_type_name" in constraint_names
