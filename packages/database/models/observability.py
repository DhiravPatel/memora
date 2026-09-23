"""Audit logs, API usage counters and AI query traces."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import AuditAction
from database.base import ID_LENGTH, Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "organization_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), index=True)
    project_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # user | api_key | system
    actor_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    action: Mapped[AuditAction] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class UsageRecord(Base):
    """Daily per-project counters backing the usage dashboard and billing."""

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("project_id", "day", "metric", name="uq_usage_records_project_day_metric"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QueryLog(Base):
    """Every answer is traceable: query → memories → source events → composed response."""

    __tablename__ = "query_logs"
    __table_args__ = (Index("ix_query_logs_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="query")  # query|context
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    event_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
