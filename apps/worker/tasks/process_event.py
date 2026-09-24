"""The main pipeline job: one event in, memories out, notifications on the way out.

Everything that happens *because* an event was understood — health recomputation, causal
links, outbound webhooks — happens here rather than inside the engine, so the engine stays
a pure transformation and this file stays the one place that describes the pipeline.
"""

from __future__ import annotations

from typing import Any

from app.services.customer_state_service import CustomerStateService
from common.enums import EventStatus
from common.logging import get_logger
from database.models import Customer, Event, Project
from database.repositories import (
    CustomerRepository,
    MemoryRepository,
    ProjectRepository,
    SignalSnapshotRepository,
)
from memory_engine import ProcessingResult
from webhooks import (
    WebhookDispatcher,
    customer_health_changed,
    event_failed,
    goal_changed,
    memory_created,
    memory_updated,
    signal_raised,
)
from worker.tasks.context import engine_for, worker_session
from worker.tasks.summarize import refresh_summary_if_drifted

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
# A single event can legitimately produce several memories; notifying on every one of a
# large batch is noise, so the tail is summarised by the health event instead.
MAX_MEMORY_EVENTS = 5
# Only a strong, newly-appeared risk is worth waking somebody for.
SIGNAL_ALERT_STRENGTH = 0.7
MAX_SIGNAL_EVENTS = 2


async def process_event(ctx: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Process a single event. Safe to retry: consolidation is idempotent per event id."""
    async with worker_session() as session:
        from database.repositories import EventRepository

        events = EventRepository(session)
        event = await session.get(Event, event_id)
        if event is None:
            logger.warning("worker.event_missing", event_id=event_id)
            return {"event_id": event_id, "status": "missing"}

        if event.status == EventStatus.PROCESSED:
            return {"event_id": event_id, "status": "already_processed"}

        project = await ProjectRepository(session).get(event.project_id)
        if project is None:
            await events.mark_status(event_id, EventStatus.FAILED, error="project deleted")
            return {"event_id": event_id, "status": "project_missing"}

        await events.mark_status(event_id, EventStatus.PROCESSING, increment_attempts=True)
        engine = engine_for(ctx, session)

        try:
            result = await engine.process_event(event=event, project=project)
        except Exception as exc:  # noqa: BLE001 - recorded on the event, then re-raised
            attempts = (event.attempts or 0) + 1
            status = EventStatus.FAILED if attempts >= MAX_ATTEMPTS else EventStatus.PENDING
            await events.mark_status(event_id, status, error=str(exc)[:1000])

            if status == EventStatus.FAILED:
                # A permanently failed event is something the customer's team should hear
                # about; it means data they sent never became memory.
                await WebhookDispatcher(session).emit(
                    event_failed(project_id=project.id, event=event, error=str(exc))
                )
            await session.commit()

            logger.error(
                "worker.event_failed", event_id=event_id, attempts=attempts, error=str(exc)
            )
            if status == EventStatus.PENDING:
                raise  # let ARQ retry with backoff
            return {"event_id": event_id, "status": "failed", "error": str(exc)[:200]}

        notifications = 0
        health_band = None
        goals_moved = 0
        trajectory = None
        lifecycle_state = None
        if result.processed:
            customer = await CustomerRepository(session).get(event.customer_id, project.id)
            if customer is not None:
                notifications = await _notify(session, project, customer, result)
                change = await engine.refresh_health(project=project, customer=customer)
                health_band = change.score.band
                if change.changed:
                    await WebhookDispatcher(session).emit(
                        customer_health_changed(
                            project_id=project.id,
                            customer=customer,
                            previous_band=change.previous_band,
                            score=change.score.score,
                            band=change.score.band,
                            explanation=change.score.as_dict()["explanation"],
                            factors=[factor.as_dict() for factor in change.score.factors],
                        )
                    )
                    notifications += 1

                # Goals and the forecast are both derived from the memories this event just
                # changed, so they are refreshed here rather than on a timer.
                refresh = await engine.refresh_goals(project=project, customer=customer)
                goals_moved = len(refresh.all)
                notifications += await _notify_goals(session, project, customer, refresh)

                trajectory, raised, report = await _refresh_signals(
                    session, engine, project, customer, change.score.score
                )
                notifications += raised

                # Last, because it reads everything above: the fact document, the
                # lifecycle move it implies, and a snapshot if anything material changed.
                # Health and the forecast are handed over rather than recomputed.
                refreshed = await CustomerStateService(session).refresh(
                    project=project,
                    customer=customer,
                    reason="event",
                    event_id=event.id,
                    health=change.score,
                    report=report,
                )
                lifecycle_state = refreshed.state
                notifications += len(refreshed.steps)

                # A rolling summary that no longer reflects the memory it summarises is
                # worse than none, so drift is checked here rather than waiting for the
                # nightly pass.
                await refresh_summary_if_drifted(
                    ctx, session, project_id=project.id, customer_id=customer.id
                )

        return {
            "event_id": event_id,
            "status": "processed" if result.processed else "skipped",
            "skipped_reason": result.skipped_reason,
            "created_memories": result.created_memory_ids,
            "updated_memories": result.updated_memory_ids,
            "duration_ms": result.duration_ms,
            "health_band": health_band,
            "goals_moved": goals_moved,
            "trajectory": trajectory,
            "lifecycle_state": lifecycle_state,
            "notifications": notifications,
        }


async def _notify(
    session: Any, project: Project, customer: Customer, result: ProcessingResult
) -> int:
    """Emit memory.created / memory.updated for what this event changed."""
    created = result.created_memory_ids[:MAX_MEMORY_EVENTS]
    updated = result.updated_memory_ids[:MAX_MEMORY_EVENTS]
    if not created and not updated:
        return 0

    memories = MemoryRepository(session)
    dispatcher = WebhookDispatcher(session)
    emitted = 0

    for memory in await memories.get_many(created, project.id):
        await dispatcher.emit(
            memory_created(project_id=project.id, memory=memory, customer=customer)
        )
        emitted += 1

    for memory in await memories.get_many(updated, project.id):
        await dispatcher.emit(
            memory_updated(
                project_id=project.id,
                memory=memory,
                customer=customer,
                reason=str((memory.meta or {}).get("last_consolidation", {}).get("reason", "")),
            )
        )
        emitted += 1

    return emitted


async def _notify_goals(
    session: Any, project: Project, customer: Customer, refresh: Any
) -> int:
    """Emit goal.achieved / goal.abandoned. Progress is too frequent to be a notification."""
    emitted = 0
    dispatcher = WebhookDispatcher(session)
    for change in refresh.changed:
        if not change.closed:
            continue
        await dispatcher.emit(goal_changed(project_id=project.id, customer=customer, change=change))
        emitted += 1
        logger.info(
            "goal.closed",
            goal_id=change.goal_id,
            customer_id=customer.id,
            status=change.status,
            reason=change.reason,
        )
    return emitted


async def _refresh_signals(
    session: Any,
    engine: Any,
    project: Project,
    customer: Customer,
    health_score: float,
) -> tuple[str | None, int, Any]:
    """Recompute the forecast, store today's reading, and alert on genuinely new risks.

    The previously stored snapshot is what makes this quiet: a risk that was already
    firing yesterday is not news, so only keys that were absent from it are emitted.
    """
    snapshots = SignalSnapshotRepository(session)
    previous = await snapshots.latest(project_id=project.id, customer_id=customer.id)
    known = {
        str(entry.get("key"))
        for entry in (previous.signals if previous else [])
        if float(entry.get("strength", 0)) >= SIGNAL_ALERT_STRENGTH
    }

    report = await engine.signals_for(
        project=project, customer=customer, health_score=health_score
    )
    await engine.record_signals(project=project, customer=customer, report=report)

    dispatcher = WebhookDispatcher(session)
    emitted = 0
    for signal in report.risks:
        if emitted >= MAX_SIGNAL_EVENTS:
            break
        if signal.strength < SIGNAL_ALERT_STRENGTH or signal.key in known:
            continue
        await dispatcher.emit(
            signal_raised(
                project_id=project.id, customer=customer, report=report, signal=signal
            )
        )
        emitted += 1
        logger.info(
            "signal.raised",
            customer_id=customer.id,
            signal=signal.key,
            strength=signal.strength,
            trajectory=str(report.trajectory),
        )
    return str(report.trajectory), emitted, report
