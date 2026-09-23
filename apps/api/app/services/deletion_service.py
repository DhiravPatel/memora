"""Data deletion.

Deletion is a first-class feature, not an afterthought: a customer can ask to be
forgotten and every derived artefact (memories, versions, embeddings, graph links,
events) must go with them. Each deletion is audited.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction
from common.errors import NotFoundError
from common.logging import get_logger
from database.models import (
    AgentSession,
    CustomerGoal,
    Embedding,
    Event,
    Memory,
    MemoryEntity,
    MemoryVersion,
)
from database.repositories import AuditRepository, CustomerRepository, MemoryRepository
from webhooks import WebhookDispatcher, customer_deleted

logger = get_logger(__name__)


@dataclass(slots=True)
class DeletionCounts:
    events: int = 0
    memories: int = 0
    embeddings: int = 0
    goals: int = 0
    agent_sessions: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "events": self.events,
            "memories": self.memories,
            "embeddings": self.embeddings,
            "goals": self.goals,
            "agent_sessions": self.agent_sessions,
        }


class DeletionService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        """``cleared=False`` hides memories the project's policy restricted.

        Nor delete one.
        """
        self.session = session
        self.cleared = cleared
        self.customers = CustomerRepository(session)
        self.memories = MemoryRepository(session, cleared=cleared)
        self.audit = AuditRepository(session)

    async def delete_customer(
        self,
        *,
        project_id: str,
        organization_id: str | None,
        customer_id: str,
        actor_type: str,
        actor_id: str | None = None,
    ) -> DeletionCounts:
        customer = await self.customers.resolve(customer_id, project_id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")

        counts = await self._count_for_customer(project_id, customer.id)
        # Cascades remove events, memories, versions, embeddings and graph links.
        await self.customers.delete(customer)
        await self.audit.record(
            action=AuditAction.DATA_DELETION,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project_id,
            resource_type="customer",
            resource_id=customer.id,
            metadata={"removed": counts.as_dict()},
        )
        await WebhookDispatcher(self.session).emit(
            customer_deleted(
                project_id=project_id, customer_id=customer.id, removed=counts.as_dict()
            )
        )
        logger.info("customer.deleted", customer_id=customer.id, removed=counts.as_dict())
        return counts

    async def delete_memory(
        self,
        *,
        project_id: str,
        organization_id: str | None,
        memory_id: str,
        actor_type: str,
        actor_id: str | None = None,
    ) -> None:
        memory = await self.memories.get(memory_id, project_id)
        if memory is None:
            raise NotFoundError("Memory not found.")
        content_hash = memory.content_hash
        await self.session.execute(
            delete(MemoryEntity).where(MemoryEntity.memory_id == memory.id)
        )
        await self.session.execute(delete(Embedding).where(Embedding.memory_id == memory.id))
        await self.session.execute(
            delete(MemoryVersion).where(MemoryVersion.memory_id == memory.id)
        )
        await self.session.delete(memory)
        await self.audit.record(
            action=AuditAction.DATA_DELETION,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project_id,
            resource_type="memory",
            resource_id=memory.id,
            metadata={"content_hash": content_hash},
        )

    async def delete_event(
        self,
        *,
        project_id: str,
        organization_id: str | None,
        event_id: str,
        actor_type: str,
        actor_id: str | None = None,
    ) -> None:
        event = await self.session.get(Event, event_id)
        if event is None or event.project_id != project_id:
            raise NotFoundError("Event not found.")
        await self.session.delete(event)
        await self.audit.record(
            action=AuditAction.DATA_DELETION,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project_id,
            resource_type="event",
            resource_id=event_id,
        )

    async def _count_for_customer(self, project_id: str, customer_id: str) -> DeletionCounts:
        events = len(
            (
                await self.session.execute(
                    select(Event.id).where(
                        Event.project_id == project_id, Event.customer_id == customer_id
                    )
                )
            ).all()
        )
        memory_ids = [
            row[0]
            for row in (
                await self.session.execute(
                    select(Memory.id).where(
                        Memory.project_id == project_id, Memory.customer_id == customer_id
                    )
                )
            ).all()
        ]
        embeddings = 0
        if memory_ids:
            embeddings = len(
                (
                    await self.session.execute(
                        select(Embedding.id).where(Embedding.memory_id.in_(memory_ids))
                    )
                ).all()
            )
        # Goals, sessions, turns and signal snapshots go with the customer row by cascade;
        # they are counted here so the API reports everything that was actually removed.
        goals = len(
            (
                await self.session.execute(
                    select(CustomerGoal.id).where(
                        CustomerGoal.project_id == project_id,
                        CustomerGoal.customer_id == customer_id,
                    )
                )
            ).all()
        )
        sessions = len(
            (
                await self.session.execute(
                    select(AgentSession.id).where(
                        AgentSession.project_id == project_id,
                        AgentSession.customer_id == customer_id,
                    )
                )
            ).all()
        )
        return DeletionCounts(
            events=events,
            memories=len(memory_ids),
            embeddings=embeddings,
            goals=goals,
            agent_sessions=sessions,
        )
