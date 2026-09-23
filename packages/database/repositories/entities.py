"""Entities and relationships (the memory graph)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select

from common.enums import EntityType, RelationshipType
from common.ids import new_id
from database.models import Entity, Relationship
from database.repositories.base import BaseRepository


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


class EntityRepository(BaseRepository):
    async def upsert(
        self,
        *,
        project_id: str,
        type: EntityType,
        name: str,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Entity:
        normalized = normalize_name(name)
        result = await self.session.execute(
            select(Entity).where(
                Entity.project_id == project_id,
                Entity.type == type.value,
                Entity.normalized_name == normalized,
            )
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            entity = Entity(
                id=new_id("ent"),
                project_id=project_id,
                type=type,
                name=name.strip(),
                normalized_name=normalized,
                external_id=external_id,
                meta=metadata or {},
            )
            self.session.add(entity)
        else:
            entity.mention_count += 1
            if external_id and not entity.external_id:
                entity.external_id = external_id
            if metadata:
                entity.meta = {**(entity.meta or {}), **metadata}
        await self.session.flush()
        return entity

    async def get(self, entity_id: str, project_id: str) -> Entity | None:
        result = await self.session.execute(
            select(Entity).where(Entity.id == entity_id, Entity.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_many(self, entity_ids: Sequence[str], project_id: str) -> list[Entity]:
        if not entity_ids:
            return []
        result = await self.session.execute(
            select(Entity).where(Entity.project_id == project_id, Entity.id.in_(list(entity_ids)))
        )
        return list(result.scalars())

    async def find_by_names(self, *, project_id: str, names: Sequence[str]) -> list[Entity]:
        normalized = [normalize_name(name) for name in names if name.strip()]
        if not normalized:
            return []
        result = await self.session.execute(
            select(Entity).where(
                Entity.project_id == project_id, Entity.normalized_name.in_(normalized)
            )
        )
        return list(result.scalars())

    async def list(
        self,
        *,
        project_id: str,
        type: EntityType | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Entity], int]:
        conditions = [Entity.project_id == project_id]
        if type:
            conditions.append(Entity.type == type.value)
        if search:
            conditions.append(Entity.normalized_name.like(f"%{normalize_name(search)}%"))
        total = await self.session.scalar(
            select(func.count()).select_from(Entity).where(*conditions)
        )
        result = await self.session.execute(
            select(Entity)
            .where(*conditions)
            .order_by(Entity.mention_count.desc(), Entity.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)


class RelationshipRepository(BaseRepository):
    async def upsert(
        self,
        *,
        project_id: str,
        source_entity_id: str,
        relationship_type: RelationshipType,
        target_entity_id: str,
        confidence: float = 0.6,
    ) -> Relationship:
        result = await self.session.execute(
            select(Relationship).where(
                Relationship.project_id == project_id,
                Relationship.source_entity_id == source_entity_id,
                Relationship.relationship_type == relationship_type.value,
                Relationship.target_entity_id == target_entity_id,
            )
        )
        relationship = result.scalar_one_or_none()
        if relationship is None:
            relationship = Relationship(
                id=new_id("rel"),
                project_id=project_id,
                source_entity_id=source_entity_id,
                relationship_type=relationship_type,
                target_entity_id=target_entity_id,
                confidence=confidence,
            )
            self.session.add(relationship)
        else:
            relationship.observation_count += 1
            # Repeated observation of the same edge increases confidence, asymptotically.
            relationship.confidence = min(
                0.99, relationship.confidence + (1 - relationship.confidence) * 0.25
            )
        await self.session.flush()
        return relationship

    async def for_entities(
        self, *, project_id: str, entity_ids: Sequence[str]
    ) -> list[Relationship]:
        if not entity_ids:
            return []
        ids = list(entity_ids)
        result = await self.session.execute(
            select(Relationship).where(
                Relationship.project_id == project_id,
                or_(
                    Relationship.source_entity_id.in_(ids),
                    Relationship.target_entity_id.in_(ids),
                ),
            )
        )
        return list(result.scalars())

    async def count(self, project_id: str) -> int:
        total = await self.session.scalar(
            select(func.count()).select_from(Relationship).where(
                Relationship.project_id == project_id
            )
        )
        return int(total or 0)
