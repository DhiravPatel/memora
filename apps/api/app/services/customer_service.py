"""Customer lifecycle beyond upsert: bulk ingest and merging duplicates.

Duplicate customer records are inevitable — someone signs up twice, a webhook arrives with
a different external id, a CRM sync creates a second row. Merging has to move *everything*
derived from the loser, including memories, links and graph edges, and leave a pointer
behind so old ids keep resolving.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
from database.models import (
    AgentAction,
    AgentApproval,
    AgentCheck,
    AgentSession,
    Customer,
    CustomerGoal,
    Event,
    Memory,
    MemoryLink,
    Project,
    SignalSnapshot,
)
from database.repositories import AuditRepository, CustomerRepository

logger = get_logger(__name__)

MAX_BATCH = 500


@dataclass(slots=True)
class MergeResult:
    source_customer_id: str
    target_customer_id: str
    events_moved: int = 0
    memories_moved: int = 0
    links_moved: int = 0
    goals_moved: int = 0
    sessions_moved: int = 0
    agent_actions_moved: int = 0
    conflicts_relinked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_customer_id": self.source_customer_id,
            "target_customer_id": self.target_customer_id,
            "events_moved": self.events_moved,
            "memories_moved": self.memories_moved,
            "links_moved": self.links_moved,
            "goals_moved": self.goals_moved,
            "sessions_moved": self.sessions_moved,
            "agent_actions_moved": self.agent_actions_moved,
        }


@dataclass(slots=True)
class BulkResult:
    created: int = 0
    updated: int = 0
    customers: list[Customer] = field(default_factory=list)


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.audit = AuditRepository(session)

    async def upsert_many(
        self, *, project: Project, records: Sequence[dict[str, Any]]
    ) -> BulkResult:
        """Create or update up to 500 customers in one request."""
        if not records:
            raise ValidationError("Provide at least one customer.")
        if len(records) > MAX_BATCH:
            raise ValidationError(f"At most {MAX_BATCH} customers per request.")

        result = BulkResult()
        for record in records:
            external_id = str(record.get("external_id", "")).strip()
            if not external_id:
                raise ValidationError("Every customer needs an external_id.")
            existing = await self.customers.get_by_external_id(external_id, project.id)
            customer = await self.customers.upsert(
                project_id=project.id,
                external_id=external_id,
                email=record.get("email"),
                name=record.get("name"),
                metadata=record.get("metadata"),
            )
            result.customers.append(customer)
            if existing is None:
                result.created += 1
            else:
                result.updated += 1

        logger.info(
            "customer.bulk_upsert",
            project_id=project.id,
            created=result.created,
            updated=result.updated,
        )
        return result

    async def merge(
        self,
        *,
        project: Project,
        source_id: str,
        target_id: str,
        actor_type: str = "api_key",
        actor_id: str | None = None,
    ) -> MergeResult:
        """Move everything from ``source`` onto ``target`` and retire the source record."""
        source = await self.customers.resolve(source_id, project.id)
        target = await self.customers.resolve(target_id, project.id)
        if source is None:
            raise NotFoundError(f"Customer '{source_id}' not found.")
        if target is None:
            raise NotFoundError(f"Customer '{target_id}' not found.")
        if source.id == target.id:
            raise ConflictError("A customer cannot be merged into itself.")

        result = MergeResult(source_customer_id=source.id, target_customer_id=target.id)

        result.events_moved = await self._move(Event, source.id, target.id)
        result.memories_moved = await self._move(Memory, source.id, target.id)
        result.links_moved = await self._move(MemoryLink, source.id, target.id)
        # Goals and agent sessions follow the person, not the record they were filed under.
        result.goals_moved = await self._move(CustomerGoal, source.id, target.id)
        result.sessions_moved = await self._move(AgentSession, source.id, target.id)
        # So does what agents did for them: the action history is what limits like "a
        # third credit this month needs a person" count (§26 4.5), and merging must not
        # reset it. The checks and approvals behind those actions go with them.
        result.agent_actions_moved = await self._move(AgentAction, source.id, target.id)
        await self._move(AgentCheck, source.id, target.id)
        await self._move(AgentApproval, source.id, target.id)
        # Signal snapshots are a per-day series keyed on customer: merging two series would
        # collide on (customer_id, captured_on), so the source's history is dropped and the
        # nightly job rebuilds the survivor's from the merged memories.
        await self.session.execute(
            delete(SignalSnapshot).where(SignalSnapshot.customer_id == source.id)
        )

        # The surviving record keeps the earliest history and the latest activity.
        if source.created_at and target.created_at and source.created_at < target.created_at:
            target.created_at = source.created_at
        if source.last_event_at and (
            target.last_event_at is None
            or ensure_utc(source.last_event_at) > ensure_utc(target.last_event_at)
        ):
            target.last_event_at = source.last_event_at
        target.meta = {**(source.meta or {}), **(target.meta or {})}
        if not target.email and source.email:
            target.email = source.email
        if not target.name and source.name:
            target.name = source.name

        # The source stays as a tombstone so its id keeps resolving to the survivor.
        source.merged_into = target.id
        source.deleted_at = utcnow()
        source.external_id = f"{source.external_id}:merged:{source.id[-8:]}"
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.DATA_DELETION,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="customer_merge",
            resource_id=target.id,
            metadata=result.as_dict(),
        )
        logger.info("customer.merged", **result.as_dict())
        return result

    async def resolve_including_merged(
        self, *, project: Project, customer_id: str
    ) -> Customer | None:
        """Resolve an id, following a merge pointer if one exists."""
        customer = await self.customers.resolve(customer_id, project.id)
        if customer is not None:
            return customer

        result = await self.session.execute(
            select(Customer).where(
                Customer.project_id == project.id,
                Customer.id == customer_id,
                Customer.merged_into.is_not(None),
            )
        )
        tombstone = result.scalar_one_or_none()
        if tombstone is None or tombstone.merged_into is None:
            return None
        return await self.customers.get(tombstone.merged_into, project.id)

    async def _move(self, model: Any, source_id: str, target_id: str) -> int:
        count = await self.session.scalar(
            select(func.count()).select_from(model).where(model.customer_id == source_id)
        )
        if not count:
            return 0
        await self.session.execute(
            update(model).where(model.customer_id == source_id).values(customer_id=target_id)
        )
        return int(count)
