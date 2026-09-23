"""Projects own API keys, customers, events and memories (the tenancy boundary)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import ID_LENGTH, Base, TimestampMixin

if TYPE_CHECKING:
    from database.models.organization import Organization

DEFAULT_PROJECT_SETTINGS: dict[str, Any] = {
    # Events below this importance are never extracted (see cost optimization).
    "min_event_importance": 0.2,
    # Per-project overrides: {"page_view": 0.05, "cancellation_requested": 0.98}
    "event_importance": {},
    "ranking_weights": {},
    "retention": {"events_days": 90, "memories_days": 0, "conversations_days": 30},
    "pii_redaction_enabled": True,
}


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_projects_organization_id_name"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Raw API keys are never stored: only an HMAC digest plus a display prefix.
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    api_key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=lambda: dict(DEFAULT_PROJECT_SETTINGS)
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="projects")

    def setting(self, key: str, default: Any = None) -> Any:
        value = (self.settings or {}).get(key)
        if value is None:
            return DEFAULT_PROJECT_SETTINGS.get(key, default)
        return value
