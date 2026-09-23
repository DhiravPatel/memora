"""Scheduled maintenance: retries, expiry, retention and queue metrics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.services.retention_service import RetentionService
from common.enums import EventStatus
from common.logging import get_logger
from common.metrics import queue_depth as queue_depth_gauge
from common.time import utcnow
from database.models import Event, Project
from worker.tasks.context import worker_session

logger = get_logger(__name__)

STUCK_AFTER_MINUTES = 15
SWEEP_LIMIT = 500


async def sweep_pending_events(ctx: dict[str, Any]) -> dict[str, Any]:
    """Re-queue events that were never picked up (for example, a Redis outage at ingest)."""
    queue = ctx["redis"]
    cutoff = utcnow() - timedelta(minutes=STUCK_AFTER_MINUTES)
    requeued = 0

    async with worker_session() as session:
        result = await session.execute(
            select(Event.id)
            .where(
                Event.status.in_([EventStatus.PENDING, EventStatus.PROCESSING]),
                Event.created_at < cutoff,
                Event.attempts < 3,
            )
            .order_by(Event.created_at)
            .limit(SWEEP_LIMIT)
        )
        for (event_id,) in result:
            await queue.enqueue_job("process_event", event_id)
            requeued += 1

    if requeued:
        logger.info("worker.sweep_requeued", count=requeued)
    return {"requeued": requeued}


async def apply_retention(ctx: dict[str, Any]) -> dict[str, Any]:
    """Enforce each project's retention windows and expire stale memories."""
    outcomes: list[dict[str, Any]] = []
    async with worker_session() as session:
        projects = list((await session.execute(select(Project))).scalars())
        service = RetentionService(session)
        for project in projects:
            outcome = await service.apply(project)
            outcomes.append(outcome.__dict__)
    return {"projects": len(outcomes), "outcomes": outcomes}


async def report_queue_depth(ctx: dict[str, Any]) -> dict[str, Any]:
    queue = ctx["redis"]
    try:
        depth = int(await queue.zcard("memory:queue"))
    except Exception:  # noqa: BLE001
        depth = 0
    queue_depth_gauge.set(depth)
    return {"queue_depth": depth}
