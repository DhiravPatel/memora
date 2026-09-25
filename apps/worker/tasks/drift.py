"""Drift detection (§26 5.5).

The event path runs the detectors an event can move — a contact changes the channel
picture, a billing event the plan's — as the event is processed. What only time moves — a
problem nobody has mentioned for a month, a feature nobody has used — is found here, once
a night, for every customer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.services.drift_service import DriftService
from common.logging import get_logger
from database.models import Customer, Event, Project
from database.repositories import CustomerRepository, ProjectRepository
from nlp.entities import contact_channel
from worker.tasks.context import worker_session

logger = get_logger(__name__)

# Customers per project per night, most recently active first — the ones whose picture is
# most likely to have moved.
BATCH = 2000
# The detectors an event can move.
EVENT_KINDS = ("channel", "plan")
_BILLING = ("invoice", "payment", "charge", "billing", "renewal")


def moves_drift(event: Event, *, memories_changed: bool) -> bool:
    """Whether processing this event can change a drift flag: a contact, a billing event
    naming a plan, or anything that changed what is remembered (a restated preference)."""
    if memories_changed:
        return True
    if contact_channel(event.event_type, event.data, event.source):
        return True
    kind = (event.event_type or "").lower()
    return any(marker in kind for marker in _BILLING) and bool((event.data or {}).get("plan"))


async def detect_for_event(session: Any, project: Project, customer: Customer | None, event: Event, *, memories_changed: bool) -> dict[str, Any] | None:
    """Run the event-moved detectors in a savepoint: a drift failure is logged, never a
    reason to undo the event's own work."""
    if customer is None or not moves_drift(event, memories_changed=memories_changed):
        return None
    try:
        async with session.begin_nested():
            run = await DriftService(session).detect(project=project, customer=customer, kinds=EVENT_KINDS)
        return run.as_dict()
    except Exception as exc:  # noqa: BLE001 - drift must never fail an event
        logger.error("drift.event_failed", event_id=event.id, customer_id=customer.id, error=str(exc))
        return None


async def detect_drift(ctx: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Every detector, for every customer of one project."""
    opened = cleared = scanned = failed = 0
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        if project is None:
            return {"project_id": project_id, "status": "missing"}
        customers, _ = await CustomerRepository(session).list(project_id=project.id, limit=BATCH)
        service = DriftService(session)
        for customer in customers:
            try:
                async with session.begin_nested():
                    run = await service.detect(project=project, customer=customer)
            except Exception as exc:  # noqa: BLE001 - one customer must not stop the sweep
                failed += 1
                logger.error("drift.customer_failed", customer_id=customer.id, error=str(exc))
                continue
            scanned += 1
            opened += len(run.opened)
            cleared += len(run.cleared)
    logger.info("drift.swept", project_id=project_id, scanned=scanned, opened=opened, cleared=cleared, failed=failed)
    return {"project_id": project_id, "scanned": scanned, "opened": opened, "cleared": cleared, "failed": failed}


async def detect_drift_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly: one sweep per project, so a big project cannot starve the rest."""
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("detect_drift", project_id)
    return {"projects": len(project_ids)}
