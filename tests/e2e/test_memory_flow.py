"""End-to-end flow against a real PostgreSQL + pgvector database.

    createdb memory_test
    psql memory_test -c 'CREATE EXTENSION vector'
    TEST_DATABASE_URL=postgresql+asyncpg://localhost/memory_test pytest tests/e2e

Covers the whole README flow: project → customer → events → memories →
consolidation → semantic query → context → deletion.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import database_required

from app.core.security import api_key_prefix, generate_api_key, hash_api_key
from app.services.deletion_service import DeletionService
from common.enums import EventStatus, MemoryStatus
from common.time import utcnow
from database.base import Base
from database.repositories import (
    CustomerRepository,
    EventRepository,
    MemoryRepository,
    OrganizationRepository,
    ProjectRepository,
)
from database.session import create_session_factory, dispose_engine, get_engine
from memory_engine import MemoryEngine
from nlp import LocalEmbedder

pytestmark = [pytest.mark.e2e, database_required]


@pytest.fixture
async def session() -> AsyncSession:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = create_session_factory()
    async with factory() as db_session:
        yield db_session
        await db_session.rollback()
    await dispose_engine()


@pytest.fixture
async def project(session: AsyncSession):
    organization = await OrganizationRepository(session).create(name="Acme", slug="acme")
    api_key = generate_api_key("test")
    created = await ProjectRepository(session).create(
        organization_id=organization.id,
        name="Production",
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key_prefix(api_key),
    )
    await session.commit()
    return created


@pytest.fixture
def engine(session: AsyncSession) -> MemoryEngine:
    return MemoryEngine(session=session, embedder=LocalEmbedder(dimensions=256))


async def ingest(session: AsyncSession, project, customer, event_type: str, data: dict, *, days_ago=0):
    return await EventRepository(session).create(
        project_id=project.id,
        customer_id=customer.id,
        event_type=event_type,
        data=data,
        occurred_at=utcnow() - timedelta(days=days_ago),
        importance=0.8,
    )


async def test_full_customer_memory_lifecycle(session, project, engine):
    customers = CustomerRepository(session)
    memories = MemoryRepository(session)

    customer = await customers.upsert(
        project_id=project.id, external_id="cus_123", name="John", email="john@acme.test"
    )

    # 1. A meaningful event produces a memory and entities.
    first = await ingest(
        session,
        project,
        customer,
        "support_message",
        {"message": "I've tried connecting Shopify three times but it still doesn't work."},
        days_ago=3,
    )
    result = await engine.process_event(event=first, project=project)
    assert result.processed
    assert result.created_memory_ids
    assert result.entity_ids

    # 2. Telemetry is filtered out before it reaches the model.
    noise = await ingest(session, project, customer, "page_view", {"path": "/dashboard"})
    noise.importance = 0.05
    noise_result = await engine.process_event(event=noise, project=project)
    assert not noise_result.processed
    # The reason names both numbers, so it is actionable without reading the settings.
    reason = noise_result.skipped_reason or ""
    assert "below this project's threshold" in reason
    assert "0.05" in reason and "0.20" in reason
    await session.refresh(noise)
    assert noise.status == EventStatus.SKIPPED

    # 3. More evidence for the same problem consolidates rather than duplicating.
    second = await ingest(
        session,
        project,
        customer,
        "support_message",
        {"message": "Shopify connection is still failing after three attempts."},
    )
    second_result = await engine.process_event(event=second, project=project)
    active, total = await memories.list(project_id=project.id, customer_id=customer.id)
    assert total <= 3, "similar statements must consolidate, not accumulate"
    assert second_result.created_memory_ids or second_result.updated_memory_ids

    # 4. Every memory keeps its version history and its source events.
    memory = active[0]
    versions = await memories.versions(memory.id)
    assert versions, "memory changes must be auditable"
    assert memory.source_event_ids

    # 5. Retrieval finds it and the answer cites evidence.
    answer = await engine.answer(
        project=project, customer=customer, query="What problems has this customer experienced?"
    )
    assert answer.answer
    assert answer.memories
    assert answer.trace["strategies"]

    # 6. Context is bounded and sectioned for an agent.
    context = await engine.build_context(
        project=project, customer=customer, task="respond_to_support_ticket"
    )
    payload = context.to_dict()
    assert payload["memory_ids"]
    assert context.token_count <= 2000

    # 7. Deleting the customer removes everything derived from them.
    await session.commit()
    counts = await DeletionService(session).delete_customer(
        project_id=project.id,
        organization_id=project.organization_id,
        customer_id=customer.id,
        actor_type="user",
        actor_id="usr_test",
    )
    await session.commit()
    assert counts.memories >= 1
    remaining, remaining_total = await memories.list(project_id=project.id, status=None)
    assert remaining_total == 0
    assert await customers.get(customer.id, project.id) is None


async def test_semantic_search_ranks_the_relevant_memory_first(session, project, engine):
    customers = CustomerRepository(session)
    customer = await customers.upsert(project_id=project.id, external_id="cus_456", name="Ada")

    await engine.process_event(
        event=await ingest(
            session, project, customer, "support_message",
            {"message": "The Shopify integration keeps failing."},
        ),
        project=project,
    )
    await engine.process_event(
        event=await ingest(
            session, project, customer, "feature_used", {"feature": "campaign_builder"}
        ),
        project=project,
    )

    retrieval = await engine.search(
        project=project, customer=customer, query="Shopify integration problems", limit=5
    )
    assert retrieval.memories
    assert "shopify" in retrieval.memories[0].memory.content.lower()


async def test_tenant_isolation(session, project, engine):
    """A project can never read another project's customers or memories."""
    other_org = await OrganizationRepository(session).create(name="Other", slug="other")
    api_key = generate_api_key("test")
    other_project = await ProjectRepository(session).create(
        organization_id=other_org.id,
        name="Other project",
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key_prefix(api_key),
    )

    customers = CustomerRepository(session)
    customer = await customers.upsert(project_id=project.id, external_id="cus_789", name="Grace")
    await engine.process_event(
        event=await ingest(
            session, project, customer, "support_message", {"message": "Stripe payouts failed."}
        ),
        project=project,
    )
    await session.commit()

    assert await customers.get(customer.id, other_project.id) is None
    memories = MemoryRepository(session)
    _, total = await memories.list(project_id=other_project.id)
    assert total == 0
    leaked = await memories.search_keyword(
        project_id=other_project.id, customer_id=None, query="Stripe", limit=10
    )
    assert leaked == []


async def test_memory_expiry_marks_transient_memories(session, project, engine):
    customers = CustomerRepository(session)
    memories = MemoryRepository(session)
    customer = await customers.upsert(project_id=project.id, external_id="cus_exp")

    memory = await memories.create(
        project_id=project.id,
        customer_id=customer.id,
        type="intent",
        content="Customer is currently evaluating Shopify.",
        importance=0.6,
        confidence=0.8,
        expires_at=utcnow() - timedelta(days=1),
    )
    expired = await memories.expire_due(project_id=project.id)
    assert expired == 1
    await session.refresh(memory)
    assert memory.status == MemoryStatus.EXPIRED


async def test_inferred_types_widen_recall_instead_of_filtering(session, project, engine):
    """"Why did this customer downgrade?" must surface the problems that explain it."""
    customer = await CustomerRepository(session).upsert(
        project_id=project.id, external_id="cus_downgrade", name="Sam"
    )
    for event_type, data, days_ago in (
        ("integration_failed", {"integration": "Shopify", "error": "OAuth timeout"}, 5),
        ("support_message", {"message": "Shopify still does not work for us."}, 3),
        ("subscription_downgraded", {"plan": "Starter", "previous_plan": "Pro"}, 0),
    ):
        await engine.process_event(
            event=await ingest(session, project, customer, event_type, data, days_ago=days_ago),
            project=project,
        )

    retrieval = await engine.search(
        project=project, customer=customer, query="Why did this customer downgrade?", limit=10
    )
    types = {str(item.memory.type) for item in retrieval.memories}
    assert "subscription" in types
    assert "problem" in types or any(
        "shopify" in item.memory.content.lower() for item in retrieval.memories
    )
    assert "type" in retrieval.strategies_used
