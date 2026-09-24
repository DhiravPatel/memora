"""Goal persistence.

Goals are small and read together with a customer, so everything here is keyed by
``(project_id, customer_id)`` and ordered so that live goals come before closed ones.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, exists, func, select

from common.enums import GoalStatus
from common.ids import new_id
from common.time import utcnow
from database.access import UNRESTRICTED, current_access, hidden_condition
from database.models import CustomerGoal, Memory
from database.repositories.base import BaseRepository

# Live goals first, then most recently touched.
_LIVE_FIRST = case(
    (CustomerGoal.status.in_([GoalStatus.ACHIEVED, GoalStatus.ABANDONED]), 1), else_=0
)


class GoalRepository(BaseRepository):
    """Goal SQL. Reader-bound the same way :class:`MemoryRepository` is.

    A goal's statement *is* the words of the memory it was born from, so a goal whose
    memory the reader may not see — restricted without clearance, or of a type outside
    their agent profile — is hidden with it. Pass ``cleared`` to get a reader-bound
    repository; omit it for the tracker and aggregates, which see everything.
    """

    def __init__(self, session: Any, *, cleared: bool | None = None) -> None:
        super().__init__(session)
        self.reader = cleared is not None
        access = current_access() if self.reader else UNRESTRICTED
        self._hidden_memory = (
            hidden_condition(cleared=bool(cleared), readable_types=access.readable_types)
            if self.reader
            else None
        )

    def _visible(self) -> list[Any]:
        if self._hidden_memory is None:
            return []
        return [
            ~exists().where(Memory.id == CustomerGoal.memory_id, self._hidden_memory)
        ]

    async def hidden_among(self, project_id: str, ids: Iterable[str]) -> frozenset[str]:
        """Which of these goal ids quote a memory the reader may not see."""
        wanted = list({ident for ident in ids if ident})
        if self._hidden_memory is None or not wanted:
            return frozenset()
        result = await self.session.execute(
            select(CustomerGoal.id).where(
                CustomerGoal.project_id == project_id,
                CustomerGoal.id.in_(wanted),
                exists().where(Memory.id == CustomerGoal.memory_id, self._hidden_memory),
            )
        )
        return frozenset(result.scalars())

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
                CustomerGoal.id == goal_id, CustomerGoal.project_id == project_id, *self._visible()
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
        filters = [CustomerGoal.project_id == project_id, *self._visible()]
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
