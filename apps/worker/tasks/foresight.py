"""Scheduled jobs for the forward-looking features.

The event path keeps signals and goals fresh for *active* customers. These jobs exist for
the ones who have gone quiet — which is precisely the population the forecast cares about:
a customer who stops sending events never triggers the event path again, so without this
their trajectory would freeze at the last thing they did.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.services.agent_service import IDLE_TIMEOUT_HOURS, AgentService, idle_cutoff
from app.services.signal_service import SignalService
from common.enums import AgentSessionStatus, GoalStatus
from common.logging import get_logger
from common.time import days_ago, utcnow
from database.models import Customer, Project
from database.repositories import (
    AgentSessionRepository,
    CustomerRepository,
    GoalRepository,
    ProjectRepository,
    SignalSnapshotRepository,
)
from memory_engine.goals.tracker import STALE_AFTER_DAYS
from worker.tasks.context import engine_for, worker_session

logger = get_logger(__name__)

SNAPSHOT_BATCH = 500
SESSION_BATCH = 200
# Snapshots are the series behind the trend chart; a quarter is plenty to see a slide.
SNAPSHOT_RETENTION_DAYS = 120


async def snapshot_signals(ctx: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Record today's forecast for every customer in one project."""
    written = 0
    declining = 0
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        if project is None:
            return {"project_id": project_id, "status": "missing"}

        customers, _ = await CustomerRepository(session).list(
            project_id=project.id, limit=SNAPSHOT_BATCH
        )
        service = SignalService(session)
        for customer in customers:
            report = await service.record_snapshot(project=project, customer=customer)
            written += 1
            if str(report.trajectory) == "declining":
                declining += 1

        removed = await SignalSnapshotRepository(session).delete_older_than(
            project_id=project.id, cutoff=days_ago(SNAPSHOT_RETENTION_DAYS).date()
        )

    logger.info(
        "signals.snapshotted",
        project_id=project_id,
        customers=written,
        declining=declining,
        pruned=removed,
    )
    return {"project_id": project_id, "customers": written, "declining": declining, "pruned": removed}


async def snapshot_signals_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly fan-out: one snapshot job per project."""
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("snapshot_signals", project_id)
    return {"projects": len(project_ids)}


async def sweep_stale_goals(ctx: dict[str, Any]) -> dict[str, Any]:
    """Mark goals that nothing has moved for a month as stalled.

    A stalled goal is still a live goal — it is the strongest "you could be useful here"
    signal the product has, so it is surfaced rather than quietly closed.
    """
    marked = 0
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
        goals = GoalRepository(session)

        for project_id in project_ids:
            project = await ProjectRepository(session).get(project_id)
            if project is None:
                continue
            stale = await goals.stale(
                project_id=project_id, before=days_ago(STALE_AFTER_DAYS), limit=SNAPSHOT_BATCH
            )
            for goal in stale:
                await goals.apply(
                    goal,
                    status=GoalStatus.STALLED,
                    evidence={
                        "kind": "stalled",
                        "at": utcnow().isoformat(),
                        "note": f"no supporting evidence for {STALE_AFTER_DAYS} days",
                    },
                )
                marked += 1
            # Deliberately no webhook: stalling is the absence of news, and a nightly burst
            # of "nothing happened" notifications would train people to ignore the stream.

    logger.info("goals.swept", stalled=marked)
    return {"stalled": marked}


async def close_idle_sessions(ctx: dict[str, Any]) -> dict[str, Any]:
    """Close agent sessions nobody has touched, so their memory is not lost.

    An agent that crashes mid-conversation would otherwise leave an open session whose
    turns never become a summary — and the next session would start blind.
    """
    closed = 0
    async with worker_session() as session:
        sessions = AgentSessionRepository(session)
        idle = await sessions.idle(before=idle_cutoff(), limit=SESSION_BATCH)
        if not idle:
            return {"closed": 0}

        engine = engine_for(ctx, session)
        service = AgentService(session, engine)
        projects = ProjectRepository(session)
        for stale in idle:
            project = await projects.get(stale.project_id)
            if project is None:
                continue
            await service.close(
                project=project,
                session_id=stale.id,
                write_summary=True,
                outcome=None,
                status=AgentSessionStatus.EXPIRED,
                actor_type="system",
            )
            closed += 1

    logger.info("agent.sessions_expired", closed=closed, idle_hours=IDLE_TIMEOUT_HOURS)
    return {"closed": closed}


async def refresh_customer_foresight(
    ctx: dict[str, Any], project_id: str, customer_id: str
) -> dict[str, Any]:
    """Recompute goals and signals for one customer on demand (used after a reprocess)."""
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        customer = await session.get(Customer, customer_id)
        if project is None or customer is None:
            return {"status": "missing"}

        engine = engine_for(ctx, session)
        refresh = await engine.refresh_goals(project=project, customer=customer)
        report = await engine.signals_for(project=project, customer=customer)
        await engine.record_signals(project=project, customer=customer, report=report)

    return {
        "customer_id": customer_id,
        "goals_moved": len(refresh.all),
        "trajectory": str(report.trajectory),
        "churn_risk": report.churn_risk,
    }
