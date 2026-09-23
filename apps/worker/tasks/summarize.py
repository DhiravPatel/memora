"""Rolling customer summaries.

A customer with eighty memories is expensive to read and easy to misread. This job keeps
one ``summary`` memory per customer, rebuilt from the current top memories, so an agent
that can only afford a few hundred tokens still gets the shape of the relationship.

The summary is extractive — it is assembled from sentences already stored — so it can
never assert anything the memories do not.

Refreshed two ways: nightly for every active customer, and immediately when a customer's
memory has *drifted* far enough from what the summary covers. Drift matters because the
nightly job leaves a customer who had a bad Tuesday being described by Monday's summary
for a day — which is exactly the customer somebody is about to read about.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.enums import MemorySource, MemoryStatus, MemoryType
from common.logging import get_logger
from common.time import utcnow
from database.models import Customer, Memory
from database.repositories import CustomerRepository, MemoryRepository, ProjectRepository
from memory_engine.engine import MemoryEngine
from nlp.answer import compose
from nlp.question import analyze as analyze_question
from worker.tasks.context import embedder_for, worker_session

logger = get_logger(__name__)

MIN_MEMORIES = 4
SUMMARY_IMPORTANCE = 0.55
SUMMARY_MARKER = "rolling_summary"

# A summary is stale once this many memories have appeared or disappeared since it was
# written, or once that many amounts to a quarter of what it covers — whichever is
# smaller. Absolute *and* relative, because 3 new memories out of 6 is a different
# customer, while 3 out of 200 is noise.
SUMMARY_DRIFT_ABSOLUTE = 5
SUMMARY_DRIFT_RATIO = 0.25


async def summarize_customer(
    ctx: dict[str, Any], project_id: str, customer_id: str
) -> dict[str, Any]:
    embedder = embedder_for(ctx)
    async with worker_session() as session:
        memories_repo = MemoryRepository(session)
        project = await ProjectRepository(session).get(project_id)
        customer = await CustomerRepository(session).get(customer_id, project_id)
        if project is None or customer is None:
            return {"summarized": False, "reason": "project or customer missing"}

        memories, _ = await memories_repo.list(
            project_id=project_id,
            customer_id=customer_id,
            status=MemoryStatus.ACTIVE,
            limit=100,
        )
        source_memories = [
            memory for memory in memories if str(memory.type) != MemoryType.SUMMARY.value
        ]
        if len(source_memories) < MIN_MEMORIES:
            return {"summarized": False, "reason": "not enough memories", "count": len(source_memories)}

        views = [MemoryEngine._to_view(memory) for memory in source_memories]
        answer = compose(
            analysis=analyze_question("summarise this customer"),
            memories=views,
            customer_name=customer.name,
        )
        content = answer.text.strip()
        if not content:
            return {"summarized": False, "reason": "empty summary"}

        existing = await _existing_summary(session, project_id, customer_id)
        if existing is not None:
            if existing.content.strip() == content:
                return {"summarized": False, "reason": "unchanged", "memory_id": existing.id}
            await memories_repo.apply_update(
                existing,
                content=content,
                confidence=answer.confidence,
                last_seen_at=utcnow(),
                reason="rolling_summary_refreshed",
            )
            memory = existing
        else:
            memory = await memories_repo.create(
                project_id=project_id,
                customer_id=customer_id,
                type=MemoryType.SUMMARY,
                content=content,
                importance=SUMMARY_IMPORTANCE,
                confidence=answer.confidence,
                source=MemorySource.CONSOLIDATION,
                metadata={"marker": SUMMARY_MARKER, "covers": [view.id for view in views][:50]},
            )

        vector = await embedder.embed_one(content)
        await memories_repo.upsert_embedding(
            project_id=project_id, memory_id=memory.id, vector=vector, model=embedder.model
        )

    logger.info("worker.summary_refreshed", customer_id=customer_id, memory_id=memory.id)
    return {"summarized": True, "memory_id": memory.id, "covers": len(source_memories)}


def drift_threshold(covered: int) -> int:
    """How far a summary may drift before it is worth rewriting."""
    return max(1, min(SUMMARY_DRIFT_ABSOLUTE, round(covered * SUMMARY_DRIFT_RATIO) or 1))


def has_drifted(*, covered: int, current: int) -> bool:
    """Whether a summary covering ``covered`` memories is stale at ``current``."""
    if covered <= 0:
        return current >= MIN_MEMORIES
    return abs(current - covered) >= drift_threshold(covered)


async def refresh_summary_if_drifted(
    ctx: dict[str, Any], session: Any, *, project_id: str, customer_id: str
) -> bool:
    """Queue a summary rebuild when the customer's memory has moved on.

    Two cheap queries on a path that has already done real work, and the rebuild itself
    happens in its own job rather than on the event's critical path.
    """
    current = await MemoryRepository(session).count_for_customer(
        project_id=project_id, customer_id=customer_id, exclude_types=[MemoryType.SUMMARY]
    )
    if current < MIN_MEMORIES:
        return False

    existing = await _existing_summary(session, project_id, customer_id)
    covered = len((existing.meta or {}).get("covers") or []) if existing is not None else 0
    if not has_drifted(covered=covered, current=current):
        return False

    queue = ctx.get("redis")
    if queue is None:
        return False
    await queue.enqueue_job("summarize_customer", project_id, customer_id)
    logger.info(
        "summary.drifted",
        customer_id=customer_id,
        covered=covered,
        current=current,
        threshold=drift_threshold(covered),
    )
    return True


async def summarize_project(
    ctx: dict[str, Any], project_id: str, limit: int = 100
) -> dict[str, Any]:
    """Refresh summaries for the most recently active customers in a project."""
    async with worker_session() as session:
        result = await session.execute(
            select(Customer.id)
            .where(Customer.project_id == project_id, Customer.deleted_at.is_(None))
            .order_by(Customer.last_event_at.desc().nulls_last())
            .limit(limit)
        )
        customer_ids = [row[0] for row in result]

    queued = 0
    for customer_id in customer_ids:
        await ctx["redis"].enqueue_job("summarize_customer", project_id, customer_id)
        queued += 1
    return {"project_id": project_id, "queued": queued}


async def _existing_summary(session: Any, project_id: str, customer_id: str) -> Memory | None:
    result = await session.execute(
        select(Memory)
        .where(
            Memory.project_id == project_id,
            Memory.customer_id == customer_id,
            Memory.type == MemoryType.SUMMARY.value,
            Memory.status == MemoryStatus.ACTIVE,
        )
        .order_by(Memory.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()
