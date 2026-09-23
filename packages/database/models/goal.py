"""Customer goals, tracked from the moment they are stated to the moment they are met.

A goal starts life as an ordinary ``goal`` memory. This table is what turns that sentence
into something with a lifecycle: it remembers which keywords the goal is *about*, so later
events can be matched against it, and it records every piece of evidence that moved it, so
"achieved" is always a claim you can audit rather than a flag someone set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import GoalStatus
from database.base import ID_LENGTH, Base, TimestampMixin


class CustomerGoal(Base, TimestampMixin):
    __tablename__ = "customer_goals"
    __table_args__ = (
        Index("ix_customer_goals_customer_status", "customer_id", "status"),
        Index("ix_customer_goals_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    # The memory that stated the goal. Deleting the memory leaves the goal orphaned rather
    # than deleting history, so the column is nullable and not cascaded.
    memory_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    # Lemmas the goal is about; later text is matched against these, not against the
    # raw sentence, so "migrate the team to SSO" is still matched by "SSO rollout is done".
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[GoalStatus] = mapped_column(
        String(32), nullable=False, default=GoalStatus.OPEN, index=True
    )
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.5)
    # Every observation that moved this goal: {kind, at, memory_id?, event_id?, note}.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(String(255))
    # Set when a human overrode the tracked status; the tracker then leaves it alone.
    overridden_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
