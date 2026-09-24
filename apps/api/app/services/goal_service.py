"""Goal reads and human overrides.

The tracker owns a goal's status right up until a person disagrees with it. An override is
recorded on the goal, audited, and respected from then on — the tracker skips overridden
goals entirely rather than fighting the person who corrected it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction, GoalStatus
from common.errors import NotFoundError
from common.logging import get_logger
from common.time import utcnow
from database.models import Customer, CustomerGoal, Project
from database.repositories import AuditRepository, CustomerRepository, GoalRepository
from memory_engine.engine import MemoryEngine
from memory_engine.goals import summarise

logger = get_logger(__name__)


@dataclass(slots=True)
class GoalCounts:
    total: int = 0
    open: int = 0
    progressing: int = 0
    achieved: int = 0
    stalled: int = 0
    abandoned: int = 0
    summary: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "open": self.open,
            "progressing": self.progressing,
            "achieved": self.achieved,
            "stalled": self.stalled,
            "abandoned": self.abandoned,
            "summary": self.summary,
        }


class GoalService:
    def __init__(self, session: AsyncSession, *, cleared: bool | None = None) -> None:
        """Pass ``cleared`` for a caller-facing service: goals born from a memory the caller
        may not see are hidden, because a goal's statement is that memory's words. Counts
        stay whole either way — a number is not a quote."""
        self.session = session
        self.goals = GoalRepository(session, cleared=cleared)
        self.all_goals = GoalRepository(session)
        self.customers = CustomerRepository(session)
        self.audit = AuditRepository(session)

    async def list_for_customer(
        self,
        *,
        project: Project,
        customer: Customer,
        status: GoalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CustomerGoal], int]:
        return await self.goals.list(
            project_id=project.id,
            customer_id=customer.id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def list_for_project(
        self,
        *,
        project: Project,
        status: GoalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CustomerGoal], int]:
        return await self.goals.list(
            project_id=project.id, status=status, limit=limit, offset=offset
        )

    async def get(self, *, project: Project, goal_id: str) -> CustomerGoal:
        goal = await self.goals.get(goal_id, project.id)
        if goal is None:
            raise NotFoundError(f"Goal '{goal_id}' not found.")
        return goal

    async def counts(self, *, project: Project, customer: Customer | None = None) -> GoalCounts:
        goals, total = await self.all_goals.list(
            project_id=project.id,
            customer_id=customer.id if customer else None,
            limit=500,
        )
        counts = GoalCounts(total=total)
        for goal in goals:
            status = str(goal.status)
            setattr(counts, status, getattr(counts, status, 0) + 1)
        counts.summary = summarise([MemoryEngine._goal_view(goal) for goal in goals])
        return counts

    async def override(
        self,
        *,
        project: Project,
        goal_id: str,
        status: GoalStatus,
        note: str | None,
        actor_type: str,
        actor_id: str | None,
    ) -> CustomerGoal:
        """Record a person's verdict on a goal and stop tracking it automatically."""
        goal = await self.get(project=project, goal_id=goal_id)
        previous = str(goal.status)
        now = utcnow()
        await self.goals.apply(
            goal,
            status=status,
            progress=1.0 if status == GoalStatus.ACHIEVED else goal.progress,
            confidence=1.0,
            evidence={
                "kind": "override",
                "at": now.isoformat(),
                "note": note,
                "actor": actor_id,
                "from": previous,
                "to": str(status),
            },
            closed_reason=note or "set by a person",
            overridden_by=actor_id or actor_type,
            at=now,
        )
        await self.audit.record(
            project_id=project.id,
            organization_id=project.organization_id,
            action=AuditAction.GOAL_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="goal",
            resource_id=goal.id,
            metadata={"from": previous, "to": str(status), "note": note},
        )
        logger.info(
            "goal.overridden",
            goal_id=goal.id,
            customer_id=goal.customer_id,
            previous=previous,
            status=str(status),
        )
        return goal

    async def resolve_customer(self, *, project: Project, customer_id: str) -> Customer:
        customer = await self.customers.resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
        return customer
