"""Dashboard overview, usage series and audit access."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.usage import AuditLogOut, OverviewOut, UsageOut, UsagePoint
from common.enums import EventStatus
from common.time import utcnow
from database.models import Project
from database.repositories import (
    AuditRepository,
    CustomerRepository,
    EntityRepository,
    EventRepository,
    MemoryRepository,
    QueryLogRepository,
    RelationshipRepository,
    UsageRepository,
)


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.events = EventRepository(session)
        self.memories = MemoryRepository(session)
        self.entities = EntityRepository(session)
        self.relationships = RelationshipRepository(session)
        self.usage_records = UsageRepository(session)
        self.queries = QueryLogRepository(session)
        self.audit = AuditRepository(session)

    async def overview(self, project: Project) -> OverviewOut:
        _, total_entities = await self.entities.list(project_id=project.id, limit=1)
        return OverviewOut(
            project_id=project.id,
            total_customers=await self.customers.count(project.id),
            total_events=await self.events.count(project.id),
            total_memories=await self.memories.count(project.id),
            events_processed=await self.events.count(project.id, EventStatus.PROCESSED),
            events_pending=await self.events.count(project.id, EventStatus.PENDING),
            events_failed=await self.events.count(project.id, EventStatus.FAILED),
            ai_queries=await self.queries.count_since(
                project_id=project.id, since=utcnow() - timedelta(days=30)
            ),
            total_entities=total_entities,
            total_relationships=await self.relationships.count(project.id),
            memories_by_type=await self.memories.count_by_type(project.id),
        )

    async def usage(self, project: Project, *, days: int = 30) -> UsageOut:
        since = (utcnow() - timedelta(days=days)).date()
        records = await self.usage_records.series(project_id=project.id, since=since)
        return UsageOut(
            project_id=project.id,
            totals=await self.usage_records.totals(project_id=project.id, since=since),
            series=[
                UsagePoint(day=record.day, metric=record.metric, count=record.count)
                for record in records
            ],
        )

    async def audit_logs(
        self, *, organization_id: str, project_id: str | None = None, limit: int = 100
    ) -> list[AuditLogOut]:
        entries = await self.audit.list(
            organization_id=organization_id, project_id=project_id, limit=limit
        )
        return [
            AuditLogOut(
                id=entry.id,
                action=str(entry.action),
                actor_type=entry.actor_type,
                actor_id=entry.actor_id,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                metadata=entry.meta or {},
                created_at=entry.created_at,
            )
            for entry in entries
        ]
