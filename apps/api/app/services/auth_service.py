"""Signup, login and token refresh."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.schemas.auth import AuthResponse, TokenPair
from app.services.serializers import organization_out, user_out
from common.enums import AuditAction, UserRole
from common.errors import AuthenticationError, ConflictError, ValidationError
from common.settings import get_settings
from database.models import User
from database.repositories import (
    AuditRepository,
    OrganizationRepository,
    UserRepository,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "organization"


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)
        self.audit = AuditRepository(session)

    async def signup(
        self,
        *,
        email: str,
        password: str,
        organization_name: str,
        name: str | None = None,
        ip_address: str | None = None,
    ) -> AuthResponse:
        if await self.users.get_by_email(email) is not None:
            raise ConflictError("An account with that email already exists.")

        slug = await self._unique_slug(slugify(organization_name))
        organization = await self.organizations.create(name=organization_name, slug=slug)
        user = await self.users.create(
            organization_id=organization.id,
            email=email,
            password_hash=hash_password(password),
            name=name,
            role=UserRole.OWNER,
        )
        await self.audit.record(
            action=AuditAction.AUTHENTICATION,
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"event": "signup"},
            ip_address=ip_address,
        )
        return AuthResponse(
            user=user_out(user),
            organization=organization_out(organization),
            tokens=self._tokens(user),
        )

    async def login(
        self, *, email: str, password: str, ip_address: str | None = None
    ) -> AuthResponse:
        user = await self.users.get_by_email(email)
        # Same error for unknown user and wrong password: do not leak which accounts exist.
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationError("Incorrect email or password.")
        if not user.is_active:
            raise AuthenticationError("This account is disabled.")

        await self.users.touch_login(user)
        organization = await self.organizations.get(user.organization_id)
        if organization is None:  # pragma: no cover - referential integrity
            raise AuthenticationError("Organization no longer exists.")

        await self.audit.record(
            action=AuditAction.AUTHENTICATION,
            actor_type="user",
            actor_id=user.id,
            organization_id=organization.id,
            metadata={"event": "login"},
            ip_address=ip_address,
        )
        return AuthResponse(
            user=user_out(user),
            organization=organization_out(organization),
            tokens=self._tokens(user),
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type="refresh")
        user = await self.users.get(str(payload.get("sub", "")))
        if user is None or not user.is_active:
            raise AuthenticationError("User no longer exists or is inactive.")
        if int(payload.get("ver", 0)) != int(user.token_version or 0):
            raise AuthenticationError("This session has been signed out.")
        return self._tokens(user)

    async def change_password(
        self,
        *,
        user: User,
        current_password: str,
        new_password: str,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Change a password and invalidate every other session."""
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect.")
        if len(new_password) < 8:
            raise ValidationError("New password must be at least 8 characters.")
        if verify_password(new_password, user.password_hash):
            raise ValidationError("New password must be different from the current one.")

        user.password_hash = hash_password(new_password)
        user.token_version = int(user.token_version or 0) + 1
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.AUTHENTICATION,
            actor_type="user",
            actor_id=user.id,
            organization_id=user.organization_id,
            metadata={"event": "password_changed"},
            ip_address=ip_address,
        )
        return self._tokens(user)

    async def sign_out_everywhere(self, *, user: User) -> TokenPair:
        """Invalidate all outstanding tokens and hand back a fresh pair."""
        user.token_version = int(user.token_version or 0) + 1
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.AUTHENTICATION,
            actor_type="user",
            actor_id=user.id,
            organization_id=user.organization_id,
            metadata={"event": "sessions_revoked"},
        )
        return self._tokens(user)

    def _tokens(self, user: User) -> TokenPair:
        settings = get_settings()
        claims = {"org": user.organization_id, "ver": int(user.token_version or 0)}
        return TokenPair(
            access_token=create_token(user.id, token_type="access", extra_claims=claims),
            refresh_token=create_token(user.id, token_type="refresh", extra_claims=claims),
            expires_in=settings.jwt_access_token_minutes * 60,
        )

    async def _unique_slug(self, base: str) -> str:
        slug = base
        suffix = 2
        while await self.organizations.get_by_slug(slug) is not None:
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug
