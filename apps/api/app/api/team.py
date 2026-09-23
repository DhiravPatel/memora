"""Organization members and invitations (dashboard, JWT auth)."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, DBSession
from app.schemas.admin import (
    InvitationCreate,
    InvitationOut,
    InvitationWithToken,
    MemberOut,
    MemberRoleUpdate,
)
from app.schemas.common import Message
from app.services.serializers import invitation_out, member_out
from app.services.team_service import TeamService
from common.enums import UserRole

router = APIRouter(prefix="/v1/organization", tags=["team"])


@router.get("/members", response_model=list[MemberOut])
async def list_members(session: DBSession, current_user: CurrentUserDep) -> list[MemberOut]:
    members = await TeamService(session).members(current_user.organization_id)
    return [member_out(member) for member in members]


@router.patch("/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    user_id: str,
    payload: MemberRoleUpdate,
    session: DBSession,
    current_user: CurrentUserDep,
) -> MemberOut:
    member = await TeamService(session).change_role(
        organization_id=current_user.organization_id,
        actor=current_user.user,
        user_id=user_id,
        role=payload.role,
    )
    return member_out(member)


@router.delete("/members/{user_id}", response_model=Message)
async def remove_member(
    user_id: str, session: DBSession, current_user: CurrentUserDep
) -> Message:
    """Deactivate a member and end their sessions. The audit trail keeps their history."""
    current_user.require(UserRole.ADMIN)
    await TeamService(session).remove(
        organization_id=current_user.organization_id,
        actor=current_user.user,
        user_id=user_id,
    )
    return Message(message="Member removed.")


@router.post("/members/{user_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    user_id: str, session: DBSession, current_user: CurrentUserDep
) -> MemberOut:
    member = await TeamService(session).reactivate(
        organization_id=current_user.organization_id,
        actor=current_user.user,
        user_id=user_id,
    )
    return member_out(member)


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(
    session: DBSession, current_user: CurrentUserDep
) -> list[InvitationOut]:
    invitations = await TeamService(session).list_invitations(current_user.organization_id)
    return [invitation_out(invitation) for invitation in invitations]


@router.post("/invitations", response_model=InvitationWithToken, status_code=201)
async def invite_member(
    payload: InvitationCreate, session: DBSession, current_user: CurrentUserDep
) -> InvitationWithToken:
    """Create a single-use invitation. Only its hash is stored."""
    created = await TeamService(session).invite(
        organization_id=current_user.organization_id,
        actor=current_user.user,
        email=payload.email,
        role=payload.role,
    )
    return InvitationWithToken(
        **invitation_out(created.invitation).model_dump(),
        token=created.token,
        accept_path=created.accept_path,
        email_queued=created.email_queued,
    )


@router.delete("/invitations/{invitation_id}", response_model=InvitationOut)
async def revoke_invitation(
    invitation_id: str, session: DBSession, current_user: CurrentUserDep
) -> InvitationOut:
    invitation = await TeamService(session).revoke_invitation(
        organization_id=current_user.organization_id,
        actor=current_user.user,
        invitation_id=invitation_id,
    )
    return invitation_out(invitation)
