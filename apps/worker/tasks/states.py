"""The nightly lifecycle sweep.

The event path keeps states and snapshots current for customers who are active. A customer
who has gone quiet never triggers it again — and a customer going quiet is exactly what
moves them from ``active`` towards ``at_risk``. So once a night every customer is
re-evaluated, the same way the forecast is (§18).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.services.customer_state_service import CustomerStateService
from common.logging import get_logger
from database.models import Project
from database.repositories import CustomerRepository, ProjectRepository
from worker.tasks.context import worker_session

logger = get_logger(__name__)

# Customers per project per night. The sweep is ordered by last activity, so a project
# larger than this re-evaluates its most recently active customers first — the ones whose
# state is most likely to have moved.
BATCH = 2000


async def refresh_customer_states(
    ctx: dict[str, Any], project_id: str, reason: str = "nightly"
) -> dict[str, Any]:
    """Re-evaluate every customer's lifecycle state and take snapshots where changed."""
    moved = snapshots = scanned = 0
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        if project is None:
            return {"project_id": project_id, "status": "missing"}

        customers, _ = await CustomerRepository(session).list(project_id=project.id, limit=BATCH)
        service = CustomerStateService(session)
        for customer in customers:
            refreshed = await service.refresh(project=project, customer=customer, reason=reason)
            scanned += 1
            moved += len(refreshed.steps)
            snapshots += 1 if refreshed.snapshot is not None else 0

    logger.info(
        "lifecycle.swept", project_id=project_id, scanned=scanned, moved=moved, snapshots=snapshots
    )
    return {"project_id": project_id, "scanned": scanned, "moved": moved, "snapshots": snapshots}


async def refresh_customer_states_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly: enqueue one sweep per project, so a big project cannot starve the rest."""
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("refresh_customer_states", project_id)
    return {"projects": len(project_ids)}
