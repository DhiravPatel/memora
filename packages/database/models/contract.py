"""Memory contracts: what each event type must look like (§26 7.1).

One row per project and event type. ``definition`` is the canonical compiled form;
``version`` increments on every change, and each event checked records the version it was
checked against. Refused events never reach the events table, so what an enforcing contract
turned away is counted here, with the latest few kept as samples.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base, TimestampMixin


class EventContract(Base, TimestampMixin):
    __tablename__ = "event_contracts"
    __table_args__ = (UniqueConstraint("project_id", "event_type", name="uq_event_contracts_type"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="warn")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    updated_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    # Events an enforcing contract refused: they are not stored, so they are counted here.
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recent_rejections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # Up to when violations were last reported to webhook subscribers, and how many
    # refusals that report had counted.
    last_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
