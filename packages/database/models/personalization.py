"""A customer's personalization, as last computed (§26 6.6).

Computed when what it reads moves — on every state refresh, which follows each processed
event and runs nightly — and stored, so a product reading it on every page load reads one
row. ``version`` is a hash of what the product sees: an unchanged customer keeps it, which
is the ETag, and a change is what ``customer.personalization_changed`` reports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base


class CustomerPersonalization(Base):
    __tablename__ = "customer_personalizations"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_customer_personalizations_customer"),
        Index("ix_customer_personalizations_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    # A hash of the project settings it was computed under: a settings change makes it stale.
    settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When what the product sees last changed — ``computed_at`` moves on every refresh.
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # What prompted the last computation: event, nightly, drift_confirmed, read, …
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="event")
