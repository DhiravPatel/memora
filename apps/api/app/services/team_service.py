"""Team management: members, roles and invitations.

Two rules the code enforces rather than documents: an organization can never lose its last
owner, and nobody can grant a role above their own.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import enqueue
from app.core.security import (
    generate_invitation_token,
    hash_invitation_token,
    hash_password,
)
from common.enums import AuditAction, InvitationStatus, UserRole
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from database.models import User, UserInvitation
from database.repositories import (
    AuditRepository,
    InvitationRepository,
    OrganizationRepository,
    UserRepository,
)
from integrations.email.templates import invitation_revoked

logger = get_logger(__name__)


@dataclass(slots=True)
class CreatedInvitation:
    invitation: UserInvitation
    token: str
    # Whether the mail was handed to the queue. False means Redis was unreachable, and the
    # accept link the API returns is now the only copy — so the dashboard says so rather
    # than implying an email is on its way.
    email_queued: bool = False

    @property
    def accept_path(self) -> str:
        return f"/accept-invitation?token={self.token}"


class TeamService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.invitations = InvitationRepository(session)
        self.organizations = OrganizationRepository(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ members

    async def members(self, organization_id: str) -> list[User]:
        return await self.users.list_for_organization(organization_id)

    async def change_role(
        self, *, organization_id: str, actor: User, user_id: str, role: UserRole
    ) -> User:
        member = await self._member(organization_id, user_id)
        actor_role = UserRole(actor.role)

        if not actor_role.can(UserRole.ADMIN):
            raise ValidationError("Only admins and owners can change roles.")
        if role.rank > actor_role.rank:
            raise ValidationError("You cannot grant a role above your own.")
        if member.id == actor.id and role.rank < actor_role.rank:
            raise ValidationError("You cannot demote yourself; ask another owner.")
        if UserRole(member.role) is UserRole.OWNER and role is not UserRole.OWNER:
            await self._assert_not_last_owner(organization_id, member.id)

        member.role = role
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=actor.id,
            organization_id=organization_id,
            resource_type="user",
            resource_id=member.id,
            metadata={"event": "role_changed", "role": role.value},
        )
        return member

    async def remove(self, *, organization_id: str, actor: User, user_id: str) -> None:
        member = await self._member(organization_id, user_id)
        if member.id == actor.id:
            raise ValidationError("You cannot remove yourself from the organization.")
        if UserRole(member.role) is UserRole.OWNER:
            await self._assert_not_last_owner(organization_id, member.id)

        # Deactivate and invalidate sessions rather than deleting: audit rows reference
        # this user, and "who did that?" must stay answerable.
        member.is_active = False
        member.token_version = int(member.token_version or 0) + 1
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=actor.id,
            organization_id=organization_id,
            resource_type="user",
            resource_id=member.id,
            metadata={"event": "removed", "email": member.email},
        )
        logger.info("team.member_removed", organization_id=organization_id, user_id=member.id)

    async def reactivate(self, *, organization_id: str, actor: User, user_id: str) -> User:
        member = await self._member(organization_id, user_id)
        member.is_active = True
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=actor.id,
            organization_id=organization_id,
            resource_type="user",
            resource_id=member.id,
            metadata={"event": "reactivated"},
        )
        return member

    # -------------------------------------------------------------- invitations

    async def invite(
        self, *, organization_id: str, actor: User, email: str, role: UserRole
    ) -> CreatedInvitation:
        actor_role = UserRole(actor.role)
        if not actor_role.can(UserRole.ADMIN):
            raise ValidationError("Only admins and owners can invite people.")
        if role.rank > actor_role.rank:
            raise ValidationError("You cannot invite someone at a role above your own.")

        normalised = email.lower().strip()
        existing_user = await self.users.get_by_email(normalised)
        if existing_user is not None and existing_user.organization_id == organization_id:
            raise ConflictError("That person is already a member of this organization.")
        if existing_user is not None:
            raise ConflictError("That email already belongs to another organization.")

        await self.invitations.expire_due(organization_id)
        if await self.invitations.find_open_for_email(organization_id, normalised):
            raise ConflictError("An invitation is already open for that email.")

        token = generate_invitation_token()
        invitation = await self.invitations.create(
            organization_id=organization_id,
            email=normalised,
            role=role,
            token_hash=hash_invitation_token(token),
            invited_by=actor.id,
        )
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=actor.id,
            organization_id=organization_id,
            resource_type="invitation",
            resource_id=invitation.id,
            metadata={"event": "invited", "email": normalised, "role": role.value},
        )
        # Enqueued rather than sent here: an admin clicking Invite must not wait on a mail
        # relay, and a relay being down must not fail the invitation that is already
        # committed. The link comes back in the response either way.
        queued = await enqueue("send_invitation_email", invitation.id, token)
        logger.info(
            "team.invited",
            organization_id=organization_id,
            email=normalised,
            email_queued=bool(queued),
        )
        return CreatedInvitation(
            invitation=invitation, token=token, email_queued=bool(queued)
        )

    async def list_invitations(self, organization_id: str) -> list[UserInvitation]:
        await self.invitations.expire_due(organization_id)
        return await self.invitations.list(organization_id)

    async def revoke_invitation(
        self, *, organization_id: str, actor: User, invitation_id: str
    ) -> UserInvitation:
        invitation = await self.invitations.get(invitation_id, organization_id)
        if invitation is None:
            raise NotFoundError("Invitation not found.")
        # ``==`` and not ``is``: the column is a String, so a row loaded from the database
        # gives a plain ``str``. Identity against the enum member is therefore always false,
        # which made every revoke of a persisted invitation fail.
        status = InvitationStatus(invitation.status)
        if status != InvitationStatus.PENDING:
            raise ConflictError(f"This invitation is already {status.value}.")

        await self.invitations.revoke(invitation)
        organization = await self.organizations.get(organization_id)
        subject, text, html = invitation_revoked(
            organization=organization.name if organization else "your team"
        )
        # Best effort, and deliberately after the revoke: the link is already dead. Telling
        # them is a courtesy, and losing the courtesy is not worth failing the request.
        await enqueue("send_email", invitation.email, subject, text, html)
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=actor.id,
            organization_id=organization_id,
            resource_type="invitation",
            resource_id=invitation.id,
            metadata={"event": "invitation_revoked"},
        )
        return invitation

    async def accept_invitation(
        self, *, token: str, password: str, name: str | None = None
    ) -> User:
        invitation = await self.invitations.get_by_token_hash(hash_invitation_token(token))
        # The same error for a wrong token and an expired one: an attacker learns nothing.
        if invitation is None or not invitation.is_open:
            raise ValidationError("This invitation is invalid or has expired.")
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters.")
        if await self.users.get_by_email(invitation.email) is not None:
            raise ConflictError("An account with that email already exists.")

        user = await self.users.create(
            organization_id=invitation.organization_id,
            email=invitation.email,
            password_hash=hash_password(password),
            name=name,
            role=UserRole(invitation.role),
        )
        await self.invitations.accept(invitation)
        await self.audit.record(
            action=AuditAction.MEMBER_CHANGE,
            actor_type="user",
            actor_id=user.id,
            organization_id=invitation.organization_id,
            resource_type="user",
            resource_id=user.id,
            metadata={"event": "invitation_accepted", "role": str(invitation.role)},
        )
        logger.info("team.invitation_accepted", user_id=user.id)
        return user

    # ------------------------------------------------------------------ helpers

    async def _member(self, organization_id: str, user_id: str) -> User:
        member = await self.users.get(user_id)
        if member is None or member.organization_id != organization_id:
            raise NotFoundError("Member not found in this organization.")
        return member

    async def _assert_not_last_owner(self, organization_id: str, user_id: str) -> None:
        members = await self.users.list_for_organization(organization_id)
        owners = [
            member
            for member in members
            if UserRole(member.role) is UserRole.OWNER and member.is_active and member.id != user_id
        ]
        if not owners:
            raise ConflictError(
                "This is the organization's last owner. Promote someone else first."
            )
