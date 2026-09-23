"""Every repository query must compile for PostgreSQL.

These exercise the real repository code paths against a session that compiles the
statement instead of executing it, so SQL mistakes (including the pgvector operators and
the full-text expressions) are caught without a live database.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from common.enums import EventStatus, MemoryStatus, MemoryType
from common.time import utcnow
from database.repositories import (
    CustomerRepository,
    EntityRepository,
    EventRepository,
    MemoryRepository,
    QueryLogRepository,
    RelationshipRepository,
    UsageRepository,
)

VECTOR = [0.1] * 256


class FakeResult:
    def scalar_one_or_none(self) -> None:
        return None

    def scalars(self) -> FakeResult:
        return self

    def unique(self) -> FakeResult:
        return self

    def first(self) -> None:
        return None

    def all(self) -> list[Any]:
        return []

    def __iter__(self):
        return iter(())

    @property
    def rowcount(self) -> int:
        return 0


class CompilingSession:
    """Compiles every statement for PostgreSQL and records the SQL."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, *args, **kwargs) -> FakeResult:
        self.statements.append(
            str(statement.compile(dialect=postgresql.dialect()))
        )
        return FakeResult()

    async def scalar(self, statement, *args, **kwargs) -> int:
        await self.execute(statement)
        return 0

    async def flush(self) -> None:
        return None

    def add(self, _instance: Any) -> None:
        return None

    async def get(self, _model: Any, _pk: Any) -> None:
        return None

    async def delete(self, _instance: Any) -> None:
        return None


@pytest.fixture
def session() -> CompilingSession:
    return CompilingSession()


async def test_semantic_search_uses_cosine_distance(session):
    await MemoryRepository(session).search_semantic(
        project_id="prj_1",
        customer_id="cus_1",
        vector=VECTOR,
        model="fake-hash-v1",
        limit=10,
        types=[MemoryType.PROBLEM],
    )
    sql = session.statements[-1]
    assert "embeddings" in sql and "memories" in sql
    assert "<=>" in sql  # pgvector cosine distance operator
    assert "ORDER BY distance" in sql


async def test_keyword_search_uses_full_text_and_falls_back(session):
    await MemoryRepository(session).search_keyword(
        project_id="prj_1", customer_id="cus_1", query="shopify integration", limit=10
    )
    full_text, fallback = session.statements[-2], session.statements[-1]
    assert "websearch_to_tsquery" in full_text and "to_tsvector" in full_text
    assert "lower(memories.content) LIKE" in fallback


async def test_temporal_entity_and_relationship_searches_compile(session):
    repository = MemoryRepository(session)
    await repository.search_temporal(
        project_id="prj_1", customer_id="cus_1", since=utcnow() - timedelta(days=30)
    )
    await repository.search_by_entities(
        project_id="prj_1", customer_id="cus_1", entity_names=["Shopify"]
    )
    await repository.search_by_entity_ids(
        project_id="prj_1", customer_id="cus_1", entity_ids=["ent_1"]
    )
    await repository.entity_ids_for_memories(["mem_1"])
    await RelationshipRepository(session).for_entities(project_id="prj_1", entity_ids=["ent_1"])
    assert len(session.statements) == 5


async def test_consolidation_candidate_lookup_compiles_with_and_without_a_vector(session):
    repository = MemoryRepository(session)
    await repository.candidates_for_consolidation(
        project_id="prj_1", customer_id="cus_1", type=MemoryType.PROBLEM, vector=VECTOR
    )
    assert "<=>" in session.statements[-1]
    await repository.candidates_for_consolidation(
        project_id="prj_1", customer_id="cus_1", type=MemoryType.PROBLEM, vector=None
    )
    assert "<=>" not in session.statements[-1]


async def test_every_tenant_scoped_read_filters_on_project_id(session):
    """The isolation rule, checked in the generated SQL rather than by review."""
    memories = MemoryRepository(session)
    await memories.list(project_id="prj_1", customer_id="cus_1")
    await memories.top_for_customer(project_id="prj_1", customer_id="cus_1")
    await memories.count_by_type("prj_1")
    await CustomerRepository(session).list(project_id="prj_1", search="john")
    await EventRepository(session).list(project_id="prj_1", status=EventStatus.PENDING)
    await EventRepository(session).recent_for_customer(project_id="prj_1", customer_id="cus_1")
    await EntityRepository(session).list(project_id="prj_1", search="shop")
    await QueryLogRepository(session).list(project_id="prj_1")

    for sql in session.statements:
        if "SELECT count(*)" in sql and "usage_records" in sql:
            continue
        assert "project_id" in sql, sql


async def test_maintenance_statements_compile(session):
    await MemoryRepository(session).expire_due(project_id="prj_1")
    assert "UPDATE memories SET status" in session.statements[-1]

    await MemoryRepository(session).delete_for_customer(project_id="prj_1", customer_id="cus_1")
    await EventRepository(session).delete_older_than(project_id="prj_1", cutoff=utcnow())
    await UsageRepository(session).totals(project_id="prj_1", since=date(2026, 1, 1))
    await UsageRepository(session).series(project_id="prj_1", since=date(2026, 1, 1))


async def test_list_filters_translate_enum_values(session):
    await MemoryRepository(session).list(
        project_id="prj_1", type=MemoryType.PROBLEM, status=MemoryStatus.SUPERSEDED
    )
    sql = session.statements[-2] if "count" in session.statements[-2] else session.statements[-1]
    assert "memories.type" in sql and "memories.status" in sql
