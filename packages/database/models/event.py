"""Immutable record of something that happened.

Events are append-only: processing writes ``status``/``processed_at`` metadata but the
payload itself is never rewritten, because every memory must remain traceable to the
exact input it was derived from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import EventStatus
from database.base import ID_LENGTH, Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "external_event_id", name="uq_events_project_id_external_event_id"
        ),
        Index("ix_events_customer_occurred_at", "customer_id", "occurred_at"),
        Index("ix_events_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="api")
    importance: Mapped[float] = mapped_column(nullable=False, default=0.0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[EventStatus] = mapped_column(
        String(32), nullable=False, default=EventStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    # Why this event did or did not become a memory: the pipeline's own decisions, as
    # ``memory_engine.explain.EventExplanation``. An event that produced nothing used to
    # look identical to one that produced three, which made "why is my memory empty?"
    # unanswerable after the fact. Null for events processed before this existed.
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
