"""Agent session and turn persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select

from common.enums import AgentSessionStatus, TurnRole
from common.ids import new_id
from common.time import utcnow
from database.models import AgentSession, AgentTurn
from database.repositories.base import BaseRepository


class AgentSessionRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        agent: str,
        external_id: str | None = None,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentSession:
        now = utcnow()
        session = AgentSession(
            id=new_id("ags"),
            project_id=project_id,
            customer_id=customer_id,
            external_id=external_id,
            agent=agent,
            channel=channel,
            status=AgentSessionStatus.OPEN,
            started_at=now,
            last_active_at=now,
            turn_count=0,
            memory_ids=[],
            meta=metadata or {},
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def get(self, session_id: str, project_id: str) -> AgentSession | None:
        result = await self.session.execute(
            select(AgentSession).where(
                AgentSession.id == session_id, AgentSession.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str, project_id: str) -> AgentSession | None:
        result = await self.session.execute(
            select(AgentSession).where(
                AgentSession.project_id == project_id, AgentSession.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        status: AgentSessionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentSession], int]:
        filters = [AgentSession.project_id == project_id]
        if customer_id:
            filters.append(AgentSession.customer_id == customer_id)
        if status:
            filters.append(AgentSession.status == status)

        total = await self.session.scalar(
            select(func.count()).select_from(AgentSession).where(*filters)
        )
        result = await self.session.execute(
            select(AgentSession)
            .where(*filters)
            .order_by(AgentSession.last_active_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def recent_closed(
        self, *, project_id: str, customer_id: str, limit: int = 3, exclude_id: str | None = None
    ) -> list[AgentSession]:
        """The last few finished sessions — what a resuming agent is told about."""
        filters = [
            AgentSession.project_id == project_id,
            AgentSession.customer_id == customer_id,
            AgentSession.status != AgentSessionStatus.OPEN,
            AgentSession.summary.isnot(None),
        ]
        if exclude_id:
            filters.append(AgentSession.id != exclude_id)
        result = await self.session.execute(
            select(AgentSession)
            .where(*filters)
            .order_by(AgentSession.closed_at.desc().nullslast())
            .limit(limit)
        )
        return list(result.scalars())

    async def touch(
        self, session: AgentSession, *, memory_ids: Sequence[str] = (), at: datetime | None = None
    ) -> None:
        session.last_active_at = at or utcnow()
        session.turn_count += 1
        if memory_ids:
            merged = [*session.memory_ids, *[mid for mid in memory_ids if mid]]
            # Deduplicated, oldest first, capped: a long session must not grow unbounded.
            session.memory_ids = list(dict.fromkeys(merged))[-200:]
        await self.session.flush()

    async def close(
        self,
        session: AgentSession,
        *,
        summary: str | None,
        summary_memory_id: str | None,
        status: AgentSessionStatus = AgentSessionStatus.CLOSED,
        at: datetime | None = None,
    ) -> AgentSession:
        session.status = status
        session.summary = summary
        session.summary_memory_id = summary_memory_id
        session.closed_at = at or utcnow()
        await self.session.flush()
        return session

    async def started_since(
        self, *, project_id: str, customer_id: str, since: datetime, limit: int = 200
    ) -> list[AgentSession]:
        """Conversations a customer started after a moment, newest first — contacts, for
        channel drift (§26 5.5)."""
        result = await self.session.execute(
            select(AgentSession)
            .where(
                AgentSession.project_id == project_id,
                AgentSession.customer_id == customer_id,
                AgentSession.started_at > since,
            )
            .order_by(AgentSession.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def idle(self, *, before: datetime, limit: int = 100) -> list[AgentSession]:
        """Open sessions nobody has touched since ``before``."""
        result = await self.session.execute(
            select(AgentSession)
            .where(
                AgentSession.status == AgentSessionStatus.OPEN,
                AgentSession.last_active_at < before,
            )
            .order_by(AgentSession.last_active_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def count_open(self, project_id: str) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(AgentSession)
                .where(
                    AgentSession.project_id == project_id,
                    AgentSession.status == AgentSessionStatus.OPEN,
                )
            )
            or 0
        )

    async def delete_for_customer(self, *, project_id: str, customer_id: str) -> int:
        result = await self.session.execute(
            delete(AgentSession).where(
                AgentSession.project_id == project_id, AgentSession.customer_id == customer_id
            )
        )
        return int(result.rowcount or 0)

    async def reassign_customer(self, *, project_id: str, source_id: str, target_id: str) -> int:
        result = await self.session.execute(
            select(AgentSession).where(
                AgentSession.project_id == project_id, AgentSession.customer_id == source_id
            )
        )
        sessions = list(result.scalars())
        for session in sessions:
            session.customer_id = target_id
        await self.session.flush()
        return len(sessions)


class AgentTurnRepository(BaseRepository):
    async def add(
        self,
        *,
        session_id: str,
        project_id: str,
        role: TurnRole,
        content: str,
        occurred_at: datetime | None = None,
        event_id: str | None = None,
        retrieved_memory_ids: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> AgentTurn:
        turn = AgentTurn(
            id=new_id("trn"),
            session_id=session_id,
            project_id=project_id,
            role=role,
            content=content,
            occurred_at=occurred_at or utcnow(),
            event_id=event_id,
            retrieved_memory_ids=list(retrieved_memory_ids),
            meta=metadata or {},
        )
        self.session.add(turn)
        await self.session.flush()
        return turn

    async def list(
        self, *, session_id: str, project_id: str, limit: int = 200
    ) -> list[AgentTurn]:
        result = await self.session.execute(
            select(AgentTurn)
            .where(AgentTurn.session_id == session_id, AgentTurn.project_id == project_id)
            .order_by(AgentTurn.occurred_at, AgentTurn.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def counts_by_session(self, session_ids: Sequence[str]) -> dict[str, int]:
        if not session_ids:
            return {}
        result = await self.session.execute(
            select(AgentTurn.session_id, func.count())
            .where(AgentTurn.session_id.in_(list(session_ids)))
            .group_by(AgentTurn.session_id)
        )
        return {session_id: int(count) for session_id, count in result.all()}
