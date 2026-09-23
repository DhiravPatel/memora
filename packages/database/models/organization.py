"""Organizations and the dashboard users that belong to them."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from common.enums import UserRole
from database.base import ID_LENGTH, Base, TimestampMixin

if TYPE_CHECKING:
    from database.models.project import Project


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    projects: Mapped[list[Project]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    users: Mapped[list[User]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False, default=UserRole.OWNER)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change or "sign out everywhere"; tokens carry the value they were
    # issued with, so older sessions stop validating immediately.
    token_version: Mapped[int] = mapped_column(nullable=False, default=0)

    organization: Mapped[Organization] = relationship(back_populates="users")
