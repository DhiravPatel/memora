"""Entities and the relationships between them (the customer memory graph)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import EntityType, RelationshipType
from database.base import ID_LENGTH, Base, TimestampMixin


class Entity(Base, TimestampMixin):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "type", "normalized_name", name="uq_entities_project_type_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[EntityType] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Case/whitespace folded name, so "Shopify" and "shopify " are the same entity.
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    mention_count: Mapped[int] = mapped_column(nullable=False, default=1)


class Relationship(Base, TimestampMixin):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            name="uq_relationships_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_entity_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    relationship_type: Mapped[RelationshipType] = mapped_column(String(64), nullable=False)
    target_entity_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.5)
    observation_count: Mapped[int] = mapped_column(nullable=False, default=1)
