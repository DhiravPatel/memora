"""Team invitations."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from common.enums import InvitationStatus, UserRole
from common.ids import new_id
from common.time import utcnow
from database.models import UserInvitation
from database.repositories.base import BaseRepository

DEFAULT_TTL_DAYS = 7


class InvitationRepository(BaseRepository):
    async def create(
        self,
        *,
        organization_id: str,
        email: str,
        role: UserRole,
        token_hash: str,
        invited_by: str | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> UserInvitation:
        invitation = UserInvitation(
            id=new_id("inv"),
            organization_id=organization_id,
            email=email.lower().strip(),
            role=role,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=utcnow() + timedelta(days=ttl_days),
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_by_token_hash(self, token_hash: str) -> UserInvitation | None:
        result = await self.session.execute(
            select(UserInvitation).where(UserInvitation.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def get_any(self, invitation_id: str) -> UserInvitation | None:
        """By id alone. For the worker, which has an id and no tenant in hand."""
        result = await self.session.execute(
            select(UserInvitation).where(UserInvitation.id == invitation_id)
        )
        return result.scalar_one_or_none()

    async def get(self, invitation_id: str, organization_id: str) -> UserInvitation | None:
        result = await self.session.execute(
            select(UserInvitation).where(
                UserInvitation.id == invitation_id,
                UserInvitation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self, organization_id: str, *, status: InvitationStatus | None = None
    ) -> list[UserInvitation]:
        conditions = [UserInvitation.organization_id == organization_id]
        if status:
            conditions.append(UserInvitation.status == status)
        result = await self.session.execute(
            select(UserInvitation).where(*conditions).order_by(UserInvitation.created_at.desc())
        )
        return list(result.scalars())

    async def find_open_for_email(
        self, organization_id: str, email: str
    ) -> UserInvitation | None:
        result = await self.session.execute(
            select(UserInvitation).where(
                UserInvitation.organization_id == organization_id,
                UserInvitation.email == email.lower().strip(),
                UserInvitation.status == InvitationStatus.PENDING,
            )
        )
        return result.scalars().first()

    async def accept(self, invitation: UserInvitation) -> UserInvitation:
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = utcnow()
        await self.session.flush()
        return invitation

    async def revoke(self, invitation: UserInvitation) -> UserInvitation:
        invitation.status = InvitationStatus.REVOKED
        await self.session.flush()
        return invitation

    async def expire_due(self, organization_id: str) -> int:
        invitations = await self.list(organization_id, status=InvitationStatus.PENDING)
        now = utcnow()
        expired = 0
        for invitation in invitations:
            if invitation.expires_at <= now:
                invitation.status = InvitationStatus.EXPIRED
                expired += 1
        if expired:
            await self.session.flush()
        return expired
