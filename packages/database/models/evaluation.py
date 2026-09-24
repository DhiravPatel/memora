"""Retrieval evaluation: questions with known answers, and every run against them.

Runs are stored rather than computed on demand because the point of an evaluation is the
comparison — *did this change make retrieval better or worse?* — and a comparison needs the
previous number to still exist after the setting that produced it has changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base, TimestampMixin


class EvalSet(Base, TimestampMixin):
    __tablename__ = "eval_sets"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_eval_sets_project_name"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))


class EvalCase(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (Index("ix_eval_cases_set", "set_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Exact, but die with reprocessing.
    expected_memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Looser, and survive it.
    expected_phrases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # "retrieval" asks a question; "extraction" (§26 4.3) sends an event through the
    # pipeline without writing and checks the memories it would make.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="retrieval", server_default="retrieval")
    # For extraction: {event_type, data, occurred_at?}.
    event: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # For extraction: {expect: [...], forbid: [...], expect_nothing: bool}.
    expectations: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_set_created", "set_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # queued | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    label: Mapped[str | None] = mapped_column(String(120))
    # Run with the starter's clearance: an evaluation must not read restricted memories on
    # behalf of someone who could not.
    cleared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    k: Mapped[int] = mapped_column(nullable=False, default=10)
    # The retrieval settings in force, so a result can be attributed to a configuration.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Settings a regression run was measured under instead of the project's (§26 4.3) —
    # proposed, never saved. Such a run is never a baseline. SQL NULL, not JSON null, when
    # absent: the baseline query filters on IS NULL.
    overrides: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    baseline_run_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
