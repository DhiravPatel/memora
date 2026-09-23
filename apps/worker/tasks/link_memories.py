"""Recompute the causal/temporal links between a customer's memories.

Links are derived from the memories as they stand, so they are rebuilt rather than
patched: a consolidation, a correction or an expiry changes the story, and a stale edge is
worse than no edge.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.logging import get_logger
from database.models import Customer
from database.repositories import CustomerRepository, ProjectRepository
from worker.tasks.context import engine_for, worker_session

logger = get_logger(__name__)


async def link_memories(ctx: dict[str, Any], project_id: str, customer_id: str) -> dict[str, Any]:
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        customer = await CustomerRepository(session).get(customer_id, project_id)
        if project is None or customer is None:
            return {"linked": 0, "reason": "project or customer missing"}
        links = await engine_for(ctx, session).refresh_links(project=project, customer=customer)
    return {"linked": len(links), "customer_id": customer_id}


async def link_project(ctx: dict[str, Any], project_id: str, limit: int = 200) -> dict[str, Any]:
    async with worker_session() as session:
        result = await session.execute(
            select(Customer.id)
            .where(Customer.project_id == project_id, Customer.deleted_at.is_(None))
            .order_by(Customer.last_event_at.desc().nulls_last())
            .limit(limit)
        )
        customer_ids = [row[0] for row in result]

    for customer_id in customer_ids:
        await ctx["redis"].enqueue_job("link_memories", project_id, customer_id)
    logger.info("worker.link_project", project_id=project_id, queued=len(customer_ids))
    return {"project_id": project_id, "queued": len(customer_ids)}
