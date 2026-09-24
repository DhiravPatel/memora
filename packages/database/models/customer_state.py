"""What we knew about a customer, when — and which lifecycle state they were in.

Two tables, one idea: the present is not enough. An agent that told a customer something
last Tuesday did so on the basis of what Memora knew last Tuesday, and debugging that
means being able to read last Tuesday back.

* ``customer_snapshots`` — an immutable copy of the fact document (§26 1.1) each time it
  changes *materially*. Unchanged state writes nothing, so the table grows with what
  happens to a customer rather than with the number of events they send.
* ``customer_states`` — the lifecycle history: one row per stay in a state, with the
  transition that caused it and the evaluation that justified it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base


class CustomerSnapshot(Base):
    __tablename__ = "customer_snapshots"
    __table_args__ = (
        Index("ix_customer_snapshots_customer_taken", "customer_id", "taken_at"),
        Index("ix_customer_snapshots_project_taken", "project_id", "taken_at"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # What prompted it: "event", "nightly", "state_change", "manual".
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="event")
    event_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    # A hash of the material facts. Equal to the previous snapshot's means nothing that
    # matters changed, and nothing is written.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # Indexed copies of the facts every list and chart filters on.
    health_score: Mapped[float | None] = mapped_column()
    health_band: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[str | None] = mapped_column(String(64))
    trajectory: Mapped[str | None] = mapped_column(String(32))
    open_problems: Mapped[int] = mapped_column(nullable=False, default=0)
    churn_risk: Mapped[float | None] = mapped_column()
    expansion_score: Mapped[float | None] = mapped_column()
    # The full document, as the system saw it, and — only when restricted memories
    # contributed — the redacted view a reader without clearance is shown.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    redacted_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # What moved since the previous snapshot: [{fact, before, after}].
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class CustomerState(Base):
    __tablename__ = "customer_states"
    __table_args__ = (
        Index("ix_customer_states_customer_entered", "customer_id", "entered_at"),
        # The current state of every customer in a project, per track, for cohort-style reads.
        Index(
            "ix_customer_states_project_current",
            "project_id",
            "track",
            "state",
            postgresql_where=text("exited_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    # Which lifecycle track this stay belongs to (§26 4.2). The primary is "lifecycle".
    track: Mapped[str] = mapped_column(String(40), nullable=False, default="lifecycle", server_default="lifecycle")
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(64))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Null while this is the customer's current state.
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "initial", "auto" (a transition fired) or "manual" (a person set it).
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    transition: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    # The condition evaluation that justified the move — leaves, outcome, evidence.
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # A person's decision holds until released, or until ``pinned_until``.
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    snapshot_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
