"""API keys.

A project starts with one key, but a real deployment needs several: an ingestion key on
the web servers, a read-only key for an agent, a short-lived key for a migration script.
Each is independently scoped, revocable and observable (``last_used_at``), and only an
HMAC digest is ever stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import ApiKeyScope
from database.base import ID_LENGTH, Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_project_active", "project_id", "revoked_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # HMAC-SHA256 of the raw key. The raw value is shown exactly once, at creation.
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: [scope.value for scope in ApiKeyScope.defaults()]
    )
    created_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    use_count: Mapped[int] = mapped_column(nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    @property
    def is_active(self) -> bool:
        from common.time import utcnow

        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > utcnow()

    def has_scope(self, scope: ApiKeyScope) -> bool:
        granted = set(self.scopes or [])
        if scope.value in granted:
            return True
        if scope in ApiKeyScope.not_implied_by_admin():
            return False
        return ApiKeyScope.ADMIN.value in granted
