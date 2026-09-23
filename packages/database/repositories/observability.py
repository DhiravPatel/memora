"""Audit logs, usage counters and AI query traces."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from common.enums import AuditAction
from common.ids import new_id
from common.time import utcnow
from database.models import AuditLog, QueryLog, UsageRecord
from database.repositories.base import BaseRepository


class AuditRepository(BaseRepository):
    async def record(
        self,
        *,
        action: AuditAction,
        actor_type: str,
        actor_id: str | None = None,
        organization_id: str | None = None,
        project_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=new_id("aud"),
            organization_id=organization_id,
            project_id=project_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=metadata or {},
            ip_address=ip_address,
            created_at=utcnow(),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        conditions = [AuditLog.organization_id == organization_id]
        if project_id:
            conditions.append(AuditLog.project_id == project_id)
        result = await self.session.execute(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars())


class UsageRepository(BaseRepository):
    async def increment(
        self, *, project_id: str, metric: str, amount: int = 1, day: date | None = None
    ) -> None:
        day = day or utcnow().date()
        result = await self.session.execute(
            select(UsageRecord).where(
                UsageRecord.project_id == project_id,
                UsageRecord.day == day,
                UsageRecord.metric == metric,
            )
        )
        record = result.scalar_one_or_none()
        now = utcnow()
        if record is None:
            record = UsageRecord(
                id=new_id("usg"),
                project_id=project_id,
                day=day,
                metric=metric,
                count=amount,
                created_at=now,
                updated_at=now,
            )
            self.session.add(record)
        else:
            record.count += amount
            record.updated_at = now
        await self.session.flush()

    async def series(
        self, *, project_id: str, since: date, until: date | None = None
    ) -> list[UsageRecord]:
        conditions = [UsageRecord.project_id == project_id, UsageRecord.day >= since]
        if until:
            conditions.append(UsageRecord.day <= until)
        result = await self.session.execute(
            select(UsageRecord).where(*conditions).order_by(UsageRecord.day)
        )
        return list(result.scalars())

    async def totals(self, *, project_id: str, since: date | None = None) -> dict[str, int]:
        conditions = [UsageRecord.project_id == project_id]
        if since:
            conditions.append(UsageRecord.day >= since)
        result = await self.session.execute(
            select(UsageRecord.metric, func.sum(UsageRecord.count))
            .where(*conditions)
            .group_by(UsageRecord.metric)
        )
        return {metric: int(total or 0) for metric, total in result}


class QueryLogRepository(BaseRepository):
    async def record(
        self,
        *,
        project_id: str,
        query: str,
        kind: str = "query",
        customer_id: str | None = None,
        answer: str | None = None,
        memory_ids: list[str] | None = None,
        event_ids: list[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
    ) -> QueryLog:
        entry = QueryLog(
            id=new_id("qry"),
            project_id=project_id,
            customer_id=customer_id,
            kind=kind,
            query=query,
            answer=answer,
            memory_ids=memory_ids or [],
            event_ids=event_ids or [],
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            created_at=utcnow(),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list(
        self, *, project_id: str, customer_id: str | None = None, limit: int = 50
    ) -> list[QueryLog]:
        conditions = [QueryLog.project_id == project_id]
        if customer_id:
            conditions.append(QueryLog.customer_id == customer_id)
        result = await self.session.execute(
            select(QueryLog).where(*conditions).order_by(QueryLog.created_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def count_since(self, *, project_id: str, since: datetime) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(QueryLog)
            .where(QueryLog.project_id == project_id, QueryLog.created_at >= since)
        )
        return int(total or 0)
