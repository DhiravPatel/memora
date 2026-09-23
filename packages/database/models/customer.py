"""Customers are the subject of every memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base, TimestampMixin


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_customers_project_id_external_id"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The identifier used by the customer's own system.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Health is recomputed after processing and stored so portfolio views and alerts read
    # the same number, and so a band change can be detected without recomputing history.
    health_score: Mapped[float | None] = mapped_column()
    health_band: Mapped[str | None] = mapped_column(String(32), index=True)
    health_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when this record was merged into another customer; kept for id resolution.
    merged_into: Mapped[str | None] = mapped_column(String(ID_LENGTH), index=True)
