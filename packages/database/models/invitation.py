"""Team invitations.

An invitation is a single-use, expiring token. Only its hash is stored, so a leaked
database does not hand anyone a seat in an organization.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import InvitationStatus, UserRole
from database.base import ID_LENGTH, Base, TimestampMixin


class UserInvitation(Base, TimestampMixin):
    __tablename__ = "user_invitations"
    __table_args__ = (Index("ix_user_invitations_org_status", "organization_id", "status"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False, default=UserRole.MEMBER)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    invited_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    status: Mapped[InvitationStatus] = mapped_column(
        String(32), nullable=False, default=InvitationStatus.PENDING
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_open(self) -> bool:
        from common.time import utcnow

        return self.status == InvitationStatus.PENDING and self.expires_at > utcnow()
