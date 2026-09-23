"""Account creation and session management for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.dependencies import CurrentUserDep, DBSession, client_ip
from app.schemas.admin import AcceptInvitationRequest, ChangePasswordRequest
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    UserOut,
)
from app.services.auth_service import AuthService
from app.services.serializers import user_out
from app.services.team_service import TeamService

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(payload: SignupRequest, session: DBSession, request: Request) -> AuthResponse:
    return await AuthService(session).signup(
        email=payload.email,
        password=payload.password,
        organization_name=payload.organization_name,
        name=payload.name,
        ip_address=client_ip(request),
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, session: DBSession, request: Request) -> AuthResponse:
    return await AuthService(session).login(
        email=payload.email, password=payload.password, ip_address=client_ip(request)
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: DBSession) -> TokenPair:
    return await AuthService(session).refresh(payload.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUserDep) -> UserOut:
    return user_out(current_user.user)


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    payload: ChangePasswordRequest,
    session: DBSession,
    current_user: CurrentUserDep,
    request: Request,
) -> TokenPair:
    """Change a password. Every other session is signed out, and new tokens are issued."""
    return await AuthService(session).change_password(
        user=current_user.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        ip_address=client_ip(request),
    )


@router.post("/sign-out-everywhere", response_model=TokenPair)
async def sign_out_everywhere(session: DBSession, current_user: CurrentUserDep) -> TokenPair:
    """Invalidate every outstanding token for this user, including on other devices."""
    return await AuthService(session).sign_out_everywhere(user=current_user.user)


@router.post("/accept-invitation", response_model=AuthResponse, status_code=201)
async def accept_invitation(
    payload: AcceptInvitationRequest, session: DBSession, request: Request
) -> AuthResponse:
    """Join an organization with a single-use invitation token."""
    user = await TeamService(session).accept_invitation(
        token=payload.token, password=payload.password, name=payload.name
    )
    return await AuthService(session).login(
        email=user.email, password=payload.password, ip_address=client_ip(request)
    )
