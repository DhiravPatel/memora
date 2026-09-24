"""Memories: what the system believes about a customer, and how that belief changed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import MemorySource, MemoryStatus, MemoryType, Sensitivity
from database.base import ID_LENGTH, Base, TimestampMixin


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_customer_status", "customer_id", "status"),
        Index("ix_memories_project_type", "project_id", "type"),
        Index("ix_memories_customer_importance", "customer_id", "importance"),
        # Backs the keyword leg of hybrid retrieval.
        Index(
            "ix_memories_content_fts",
            func.to_tsvector(text("'english'"), text("content")),
            postgresql_using="gin",
        ),
        # Backs the concept leg: `concepts && :query_concepts` is an index scan.
        Index("ix_memories_concepts", "concepts", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[MemoryType] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Hash of the normalised content: exact-duplicate detection before any comparison work.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    importance: Mapped[float] = mapped_column(nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.5)
    status: Mapped[MemoryStatus] = mapped_column(
        String(32), nullable=False, default=MemoryStatus.ACTIVE
    )
    source: Mapped[MemorySource] = mapped_column(
        String(32), nullable=False, default=MemorySource.EVENT
    )
    # Set from the project's restriction policy when the memory is written. Indexed
    # because every read path filters on it.
    sensitivity: Mapped[Sensitivity] = mapped_column(
        String(16), nullable=False, default=Sensitivity.NORMAL, index=True
    )
    # What the memory is *about*, as concept ids from ``nlp.concepts`` — the paraphrase
    # families that let "the connector stopped functioning" answer a question about "the
    # integration being broken". Written with the content, on every write path, by the
    # repository. Null only for memories written before concepts existed, until the
    # backfill reaches them.
    concepts: Mapped[list[str] | None] = mapped_column(ARRAY(String(48)))
    # Every event that contributed evidence to this memory, newest last.
    source_event_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_count: Mapped[int] = mapped_column(nullable=False, default=1)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    superseded_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))


class MemoryVersion(Base):
    """Audit trail: a memory is never silently rewritten."""

    __tablename__ = "memory_versions"
    __table_args__ = (Index("ix_memory_versions_memory_created", "memory_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    previous_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    previous_importance: Mapped[float | None] = mapped_column()
    new_importance: Mapped[float | None] = mapped_column()
    previous_confidence: Mapped[float | None] = mapped_column()
    new_confidence: Mapped[float | None] = mapped_column()
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryEntity(Base):
    """Link table powering entity-scoped retrieval."""

    __tablename__ = "memory_entities"
    __table_args__ = (
        UniqueConstraint("memory_id", "entity_id", name="uq_memory_entities_memory_id_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
