"""Customer lookup and upsert. Every method is scoped to a project."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from common.ids import new_id
from database.models import Customer
from database.repositories.base import BaseRepository


class CustomerRepository(BaseRepository):
    async def get(self, customer_id: str, project_id: str) -> Customer | None:
        result = await self.session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.project_id == project_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str, project_id: str) -> Customer | None:
        result = await self.session.execute(
            select(Customer).where(
                Customer.project_id == project_id,
                Customer.external_id == external_id,
                Customer.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def resolve(self, identifier: str, project_id: str) -> Customer | None:
        """Accept either an internal ``cus_…`` id or the caller's own external id."""
        by_id = await self.get(identifier, project_id)
        if by_id is not None:
            return by_id
        return await self.get_by_external_id(identifier, project_id)

    async def upsert(
        self,
        *,
        project_id: str,
        external_id: str,
        email: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Customer:
        customer = await self.get_by_external_id(external_id, project_id)
        if customer is None:
            customer = Customer(
                id=new_id("cus"),
                project_id=project_id,
                external_id=external_id,
                email=email,
                name=name,
                meta=metadata or {},
            )
            self.session.add(customer)
        else:
            if email:
                customer.email = email
            if name:
                customer.name = name
            if metadata:
                customer.meta = {**(customer.meta or {}), **metadata}
        await self.session.flush()
        return customer

    async def list(
        self,
        *,
        project_id: str,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Customer], int]:
        conditions = [Customer.project_id == project_id, Customer.deleted_at.is_(None)]
        if search:
            pattern = f"%{search.lower()}%"
            conditions.append(
                func.lower(Customer.external_id).like(pattern)
                | func.lower(func.coalesce(Customer.email, "")).like(pattern)
                | func.lower(func.coalesce(Customer.name, "")).like(pattern)
            )
        total = await self.session.scalar(
            select(func.count()).select_from(Customer).where(*conditions)
        )
        result = await self.session.execute(
            select(Customer)
            .where(*conditions)
            .order_by(Customer.last_event_at.desc().nulls_last(), Customer.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def touch_last_event(self, customer: Customer, occurred_at: datetime) -> None:
        if customer.last_event_at is None or customer.last_event_at < occurred_at:
            customer.last_event_at = occurred_at
            await self.session.flush()

    async def count(self, project_id: str) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.project_id == project_id, Customer.deleted_at.is_(None))
        )
        return int(total or 0)

    async def delete(self, customer: Customer) -> None:
        """Hard delete: memories, events and embeddings cascade from the customer row."""
        await self.session.delete(customer)
