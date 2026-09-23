"""Reprocessing jobs: re-derive memories from events that are already stored.

Useful after a prompt change or a model upgrade. Events are immutable, so replaying them
is always safe; consolidation keeps the result from duplicating existing memories.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.enums import EventStatus
from common.logging import get_logger
from database.models import Event
from database.repositories import EventRepository, ProjectRepository
from worker.tasks.context import engine_for, worker_session

logger = get_logger(__name__)


async def reprocess_event(ctx: dict[str, Any], event_id: str) -> dict[str, Any]:
    async with worker_session() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return {"event_id": event_id, "status": "missing"}
        project = await ProjectRepository(session).get(event.project_id)
        if project is None:
            return {"event_id": event_id, "status": "project_missing"}

        result = await engine_for(ctx, session).process_event(event=event, project=project)
        return {
            "event_id": event_id,
            "status": "processed" if result.processed else "skipped",
            "created_memories": result.created_memory_ids,
            "updated_memories": result.updated_memory_ids,
        }


async def reprocess_customer(
    ctx: dict[str, Any], project_id: str, customer_id: str, limit: int = 200
) -> dict[str, Any]:
    """Replay a customer's history in the order it happened."""
    queued = 0
    async with worker_session() as session:
        result = await session.execute(
            select(Event.id)
            .where(Event.project_id == project_id, Event.customer_id == customer_id)
            .order_by(Event.occurred_at)
            .limit(limit)
        )
        event_ids = [row[0] for row in result]

    for event_id in event_ids:
        await ctx["redis"].enqueue_job("reprocess_event", event_id)
        queued += 1

    logger.info("worker.reprocess_customer", customer_id=customer_id, queued=queued)
    return {"customer_id": customer_id, "queued": queued}


async def retry_failed_events(
    ctx: dict[str, Any], project_id: str, limit: int = 100
) -> dict[str, Any]:
    queued = 0
    async with worker_session() as session:
        events, _ = await EventRepository(session).list(
            project_id=project_id, status=EventStatus.FAILED, limit=limit
        )
        for event in events:
            await EventRepository(session).mark_status(event.id, EventStatus.PENDING, error=None)
            await ctx["redis"].enqueue_job("process_event", event.id)
            queued += 1
    return {"project_id": project_id, "requeued": queued}
