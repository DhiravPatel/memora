"""Customer health: single customer and portfolio views.

Health is derived from memory, so it needs no separate analytics store and no nightly
job — it is computed from the same rows the API already serves, which means it can never
disagree with what the dashboard shows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from common.errors import NotFoundError
from database.models import Customer, Project
from database.repositories import CustomerRepository, EventRepository, MemoryRepository
from memory_engine.analytics import HealthScore, compute
from memory_engine.engine import MemoryEngine

PORTFOLIO_LIMIT = 200


@dataclass(slots=True)
class CustomerHealth:
    customer_id: str
    external_id: str
    name: str | None
    health: HealthScore

    def as_dict(self) -> dict[str, object]:
        return {
            "customer_id": self.customer_id,
            "external_id": self.external_id,
            "name": self.name,
            **self.health.as_dict(),
        }


class HealthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.memories = MemoryRepository(session)
        self.events = EventRepository(session)

    async def for_customer(self, *, project: Project, customer: Customer) -> CustomerHealth:
        memories, _ = await self.memories.list(
            project_id=project.id, customer_id=customer.id, limit=200
        )
        event_counts = await self.events.counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        score = compute(
            memories=[MemoryEngine._to_view(memory) for memory in memories],
            event_count=event_counts.get(customer.id, 0),
            last_event_at=customer.last_event_at,
            distinct_features=features.get(customer.id, 0),
            weights=(project.settings or {}).get("health_weights"),
        )
        return CustomerHealth(
            customer_id=customer.id,
            external_id=customer.external_id,
            name=customer.name,
            health=score,
        )

    async def portfolio(
        self,
        *,
        project: Project,
        limit: int = 50,
        bands: tuple[str, ...] = ("at_risk", "critical"),
    ) -> list[CustomerHealth]:
        """Customers ordered by risk, computed in three queries rather than N."""
        customers, _ = await self.customers.list(project_id=project.id, limit=PORTFOLIO_LIMIT)
        if not customers:
            return []

        customer_ids = [customer.id for customer in customers]
        memories_by_customer = await self.memories.active_for_customers(
            project_id=project.id, customer_ids=customer_ids
        )
        event_counts = await self.events.counts_by_customer(
            project_id=project.id, customer_ids=customer_ids
        )
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=customer_ids
        )

        weights = (project.settings or {}).get("health_weights")
        scored: list[CustomerHealth] = []
        for customer in customers:
            score = compute(
                memories=[
                    MemoryEngine._to_view(memory)
                    for memory in memories_by_customer.get(customer.id, [])
                ],
                event_count=event_counts.get(customer.id, 0),
                last_event_at=customer.last_event_at,
                distinct_features=features.get(customer.id, 0),
                weights=weights,
            )
            if bands and score.band not in bands:
                continue
            scored.append(
                CustomerHealth(
                    customer_id=customer.id,
                    external_id=customer.external_id,
                    name=customer.name,
                    health=score,
                )
            )

        scored.sort(key=lambda item: item.health.score)
        return scored[:limit]

    async def resolve_customer(self, *, project: Project, customer_id: str) -> Customer:
        customer = await self.customers.resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
        return customer
