"""Daily signal snapshots: the series that makes "declining" a measurement, not a feeling.

Signals themselves are computed on demand from memory, like health. This table stores one
compact row per customer per day so trajectory can be read from history rather than
re-derived, and so a chart of risk over time is a single indexed query. One row per day
bounds the growth: a project with 10k customers writes 10k rows a day at most, and
retention prunes them with everything else.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"
    __table_args__ = (
        UniqueConstraint("customer_id", "captured_on", name="uq_signal_snapshots_customer_id_day"),
        Index("ix_signal_snapshots_project_captured", "project_id", "captured_on"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)
    health_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    churn_risk: Mapped[float] = mapped_column(nullable=False, default=0.0)
    expansion_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    trajectory: Mapped[str] = mapped_column(String(32), nullable=False, default="steady")
    # The signal keys that were active, with their strengths — enough to explain a
    # historical row without recomputing it against memories that have since changed.
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
