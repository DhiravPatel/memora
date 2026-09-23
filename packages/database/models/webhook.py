"""Outbound webhooks: endpoints a project subscribes with, and every delivery attempt.

Deliveries are rows rather than fire-and-forget HTTP calls, because "did the customer's
system actually receive the churn alert?" has to be answerable after the fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import DeliveryStatus
from database.base import ID_LENGTH, Base, TimestampMixin
from database.types import EncryptedSecret

# An endpoint that keeps failing is disabled rather than retried forever.
MAX_CONSECUTIVE_FAILURES = 20


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (Index("ix_webhook_endpoints_project_active", "project_id", "is_active"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    # Shared secret used to sign payloads; the receiver needs the same value to verify.
    # Signing needs the value back, so this cannot be hashed — it is encrypted at rest
    # instead, transparently, by the column type.
    secret: Mapped[str] = mapped_column(EncryptedSecret(512), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    def wants(self, event_type: str) -> bool:
        """An endpoint with no event types subscribes to everything."""
        return not self.event_types or event_type in self.event_types


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_status_scheduled", "status", "scheduled_at"),
        Index("ix_webhook_deliveries_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[DeliveryStatus] = mapped_column(
        String(32), nullable=False, default=DeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    response_status: Mapped[int | None] = mapped_column()
    response_body: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column()
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
