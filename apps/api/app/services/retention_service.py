"""Retention enforcement.

Each project configures how long raw events, memories and conversation traces are kept.
The worker runs this on a schedule; nothing is deleted without a configured window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AgentSessionStatus
from common.logging import get_logger
from common.settings import Settings, get_settings
from common.time import utcnow
from database.models import AgentSession, Event, Memory, Project, QueryLog
from database.repositories import MemoryRepository

logger = get_logger(__name__)


@dataclass(slots=True)
class RetentionOutcome:
    project_id: str
    events_deleted: int = 0
    memories_expired: int = 0
    memories_deleted: int = 0
    query_logs_deleted: int = 0
    agent_sessions_deleted: int = 0


class RetentionService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.memories = MemoryRepository(session)

    async def apply(self, project: Project) -> RetentionOutcome:
        outcome = RetentionOutcome(project_id=project.id)
        retention = (project.settings or {}).get("retention") or {}
        now = utcnow()

        outcome.memories_expired = await self.memories.expire_due(project_id=project.id, now=now)

        event_days = int(retention.get("events_days", self.settings.retention_event_days))
        if event_days > 0:
            cutoff = now - timedelta(days=event_days)
            result = await self.session.execute(
                select(Event.id).where(Event.project_id == project.id, Event.occurred_at < cutoff)
            )
            ids = [row[0] for row in result]
            if ids:
                await self.session.execute(delete(Event).where(Event.id.in_(ids)))
                outcome.events_deleted = len(ids)

        memory_days = int(retention.get("memories_days", self.settings.retention_memory_days))
        if memory_days > 0:
            cutoff = now - timedelta(days=memory_days)
            result = await self.session.execute(
                select(Memory.id).where(
                    Memory.project_id == project.id, Memory.last_seen_at < cutoff
                )
            )
            ids = [row[0] for row in result]
            if ids:
                await self.session.execute(delete(Memory).where(Memory.id.in_(ids)))
                outcome.memories_deleted = len(ids)

        conversation_days = int(
            retention.get("conversations_days", self.settings.retention_conversation_days)
        )
        if conversation_days > 0:
            cutoff = now - timedelta(days=conversation_days)
            result = await self.session.execute(
                select(QueryLog.id).where(
                    QueryLog.project_id == project.id, QueryLog.created_at < cutoff
                )
            )
            ids = [row[0] for row in result]
            if ids:
                await self.session.execute(delete(QueryLog).where(QueryLog.id.in_(ids)))
                outcome.query_logs_deleted = len(ids)

            # Finished agent conversations are raw customer text and age out on the same
            # window. What the conversation *established* survives: the summary memory is
            # an ordinary memory governed by ``memories_days``. Open sessions are left
            # alone — they have not finished saying anything yet.
            result = await self.session.execute(
                select(AgentSession.id).where(
                    AgentSession.project_id == project.id,
                    AgentSession.status != AgentSessionStatus.OPEN,
                    AgentSession.last_active_at < cutoff,
                )
            )
            ids = [row[0] for row in result]
            if ids:
                await self.session.execute(delete(AgentSession).where(AgentSession.id.in_(ids)))
                outcome.agent_sessions_deleted = len(ids)

        # asdict, not __dict__: this is a slots dataclass and has no instance dict.
        logger.info("retention.applied", **asdict(outcome))
        return outcome
