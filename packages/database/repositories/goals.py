"""Goal persistence.

Goals are small and read together with a customer, so everything here is keyed by
``(project_id, customer_id)`` and ordered so that live goals come before closed ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, select

from common.enums import GoalStatus
from common.ids import new_id
from common.time import utcnow
from database.models import CustomerGoal
from database.repositories.base import BaseRepository

# Live goals first, then most recently touched.
_LIVE_FIRST = case(
    (CustomerGoal.status.in_([GoalStatus.ACHIEVED, GoalStatus.ABANDONED]), 1), else_=0
)


class GoalRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        statement: str,
        keywords: Sequence[str],
        memory_id: str | None = None,
        confidence: float = 0.5,
        opened_at: datetime | None = None,
        evidence: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CustomerGoal:
        now = opened_at or utcnow()
        goal = CustomerGoal(
            id=new_id("goal"),
            project_id=project_id,
            customer_id=customer_id,
            memory_id=memory_id,
            statement=statement,
            keywords=list(keywords),
            status=GoalStatus.OPEN,
            progress=0.0,
            confidence=confidence,
            evidence=evidence or [],
            opened_at=now,
            last_signal_at=now,
            meta=metadata or {},
        )
        self.session.add(goal)
        await self.session.flush()
        return goal

    async def get(self, goal_id: str, project_id: str) -> CustomerGoal | None:
        result = await self.session.execute(
            select(CustomerGoal).where(
                CustomerGoal.id == goal_id, CustomerGoal.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        status: GoalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CustomerGoal], int]:
        filters = [CustomerGoal.project_id == project_id]
        if customer_id:
            filters.append(CustomerGoal.customer_id == customer_id)
        if status:
            filters.append(CustomerGoal.status == status)

        total = await self.session.scalar(
            select(func.count()).select_from(CustomerGoal).where(*filters)
        )
        result = await self.session.execute(
            select(CustomerGoal)
            .where(*filters)
            .order_by(_LIVE_FIRST, CustomerGoal.last_signal_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def live_for_customer(
        self, *, project_id: str, customer_id: str, limit: int = 50
    ) -> list[CustomerGoal]:
        """Goals the tracker should still be matching new evidence against."""
        result = await self.session.execute(
            select(CustomerGoal)
            .where(
                CustomerGoal.project_id == project_id,
                CustomerGoal.customer_id == customer_id,
                CustomerGoal.status.notin_([GoalStatus.ACHIEVED, GoalStatus.ABANDONED]),
            )
            .order_by(CustomerGoal.last_signal_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def for_customers(
        self, *, project_id: str, customer_ids: Sequence[str]
    ) -> dict[str, list[CustomerGoal]]:
        """Live goals for many customers in one query, for portfolio views."""
        if not customer_ids:
            return {}
        result = await self.session.execute(
            select(CustomerGoal)
            .where(
                CustomerGoal.project_id == project_id,
                CustomerGoal.customer_id.in_(list(customer_ids)),
                CustomerGoal.status.notin_([GoalStatus.ACHIEVED, GoalStatus.ABANDONED]),
            )
            .order_by(CustomerGoal.last_signal_at.desc())
        )
        grouped: dict[str, list[CustomerGoal]] = {}
        for goal in result.scalars():
            grouped.setdefault(goal.customer_id, []).append(goal)
        return grouped

    async def apply(
        self,
        goal: CustomerGoal,
        *,
        status: GoalStatus | None = None,
        progress: float | None = None,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        closed_reason: str | None = None,
        overridden_by: str | None = None,
        at: datetime | None = None,
    ) -> CustomerGoal:
        """Record a change to a goal, keeping the evidence trail append-only."""
        now = at or utcnow()
        if status is not None and status != goal.status:
            goal.status = status
            if status.is_closed:
                goal.closed_at = now
                goal.closed_reason = closed_reason
            else:
                goal.closed_at = None
                goal.closed_reason = None
        if progress is not None:
            goal.progress = max(0.0, min(1.0, progress))
        if confidence is not None:
            goal.confidence = max(0.0, min(1.0, confidence))
        if overridden_by is not None:
            goal.overridden_by = overridden_by
        if evidence:
            # Reassigned rather than appended: SQLAlchemy does not track JSONB mutation.
            goal.evidence = [*goal.evidence, evidence][-50:]
            goal.last_signal_at = now
        await self.session.flush()
        return goal

    async def counts_by_status(self, project_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(CustomerGoal.status, func.count())
            .where(CustomerGoal.project_id == project_id)
            .group_by(CustomerGoal.status)
        )
        return {str(status): int(count) for status, count in result.all()}

    async def stale(
        self, *, project_id: str, before: datetime, limit: int = 200
    ) -> list[CustomerGoal]:
        """Open goals with no supporting evidence since ``before``."""
        result = await self.session.execute(
            select(CustomerGoal)
            .where(
                CustomerGoal.project_id == project_id,
                CustomerGoal.status.in_([GoalStatus.OPEN, GoalStatus.PROGRESSING]),
                CustomerGoal.last_signal_at < before,
                CustomerGoal.overridden_by.is_(None),
            )
            .order_by(CustomerGoal.last_signal_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def delete_for_customer(self, *, project_id: str, customer_id: str) -> int:
        result = await self.session.execute(
            delete(CustomerGoal).where(
                CustomerGoal.project_id == project_id, CustomerGoal.customer_id == customer_id
            )
        )
        return int(result.rowcount or 0)

    async def reassign_customer(self, *, project_id: str, source_id: str, target_id: str) -> int:
        """Move goals when two customer records are merged."""
        result = await self.session.execute(
            select(CustomerGoal).where(
                CustomerGoal.project_id == project_id, CustomerGoal.customer_id == source_id
            )
        )
        goals = list(result.scalars())
        for goal in goals:
            goal.customer_id = target_id
        await self.session.flush()
        return len(goals)
