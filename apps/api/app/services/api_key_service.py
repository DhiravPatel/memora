"""API key lifecycle.

Keys are created named and scoped, shown once, tracked on use, and revoked rather than
deleted — an audit trail that says "this key existed, did these things, and was turned off
at this time" is worth more than a tidy table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import api_key_prefix, generate_api_key, hash_api_key
from common.enums import ApiKeyScope, AuditAction
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.settings import get_settings
from common.time import utcnow
from database.models import ApiKey, Project
from database.repositories import AgentProfileRepository, ApiKeyRepository, AuditRepository

logger = get_logger(__name__)

MAX_ACTIVE_KEYS = 25
MAX_TTL_DAYS = 3650


@dataclass(slots=True)
class CreatedKey:
    key: ApiKey
    plaintext: str


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.keys = ApiKeyRepository(session)
        self.audit = AuditRepository(session)

    async def create(
        self,
        *,
        project: Project,
        name: str,
        scopes: list[str] | None = None,
        expires_in_days: int | None = None,
        actor_id: str | None = None,
        agent_profile_id: str | None = None,
    ) -> CreatedKey:
        if not name.strip():
            raise ValidationError("An API key needs a name so it can be recognised later.")
        if await self.keys.count_active(project.id) >= MAX_ACTIVE_KEYS:
            raise ConflictError(
                f"This project already has {MAX_ACTIVE_KEYS} active keys. Revoke one first."
            )

        requested = scopes or [scope.value for scope in ApiKeyScope.defaults()]
        validated = self._validate_scopes(requested)
        profile_id = await self._validate_profile(project, agent_profile_id, validated)

        expires_at: datetime | None = None
        if expires_in_days is not None:
            if not 1 <= expires_in_days <= MAX_TTL_DAYS:
                raise ValidationError(f"expires_in_days must be between 1 and {MAX_TTL_DAYS}.")
            expires_at = utcnow() + timedelta(days=expires_in_days)

        environment = "test" if get_settings().app_env != "production" else "live"
        plaintext = generate_api_key(environment)
        key = await self.keys.create(
            project_id=project.id,
            name=name.strip(),
            key_hash=hash_api_key(plaintext),
            key_prefix=api_key_prefix(plaintext),
            scopes=validated,
            created_by=actor_id,
            expires_at=expires_at,
        )
        key.agent_profile_id = profile_id
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.KEY_CHANGE,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="api_key",
            resource_id=key.id,
            metadata={
                "event": "created",
                "name": key.name,
                "scopes": validated,
                "agent_profile_id": profile_id,
            },
        )
        logger.info("api_key.created", project_id=project.id, key_id=key.id, scopes=validated)
        return CreatedKey(key=key, plaintext=plaintext)

    async def list(self, project: Project, *, include_revoked: bool = False) -> list[ApiKey]:
        return await self.keys.list(project.id, include_revoked=include_revoked)

    async def revoke(self, *, project: Project, key_id: str, actor_id: str | None = None) -> ApiKey:
        key = await self.keys.get(key_id, project.id)
        if key is None:
            raise NotFoundError("API key not found.")
        if key.revoked_at is not None:
            return key
        if await self.keys.count_active(project.id) <= 1:
            raise ConflictError(
                "This is the project's last active key. Create a replacement before revoking it."
            )

        await self.keys.revoke(key)
        await self.audit.record(
            action=AuditAction.KEY_CHANGE,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="api_key",
            resource_id=key.id,
            metadata={"event": "revoked", "name": key.name},
        )
        logger.info("api_key.revoked", project_id=project.id, key_id=key.id)
        return key

    async def update_scopes(
        self, *, project: Project, key_id: str, scopes: list[str], actor_id: str | None = None
    ) -> ApiKey:
        return await self.update(project=project, key_id=key_id, scopes=scopes, actor_id=actor_id)

    async def update(
        self,
        *,
        project: Project,
        key_id: str,
        scopes: list[str] | None = None,
        agent_profile_id: str | None = None,
        rebind: bool = False,
        actor_id: str | None = None,
    ) -> ApiKey:
        """Change a key's scopes, its agent profile (``rebind``), or both."""
        key = await self.keys.get(key_id, project.id)
        if key is None:
            raise NotFoundError("API key not found.")
        validated = self._validate_scopes(scopes) if scopes is not None else list(key.scopes or [])
        profile_id = key.agent_profile_id
        if rebind:
            profile_id = await self._validate_profile(project, agent_profile_id or None, validated)
        else:
            await self._validate_profile(project, profile_id, validated)
        changes: dict[str, object] = {}
        if scopes is not None:
            await self.keys.update_scopes(key, validated)
            changes["scopes"] = validated
        if rebind and profile_id != key.agent_profile_id:
            changes["agent_profile_id"] = {"from": key.agent_profile_id, "to": profile_id}
            key.agent_profile_id = profile_id
            await self.session.flush()
        if changes:
            await self.audit.record(
                action=AuditAction.KEY_CHANGE,
                actor_type="user" if actor_id else "system",
                actor_id=actor_id,
                organization_id=project.organization_id,
                project_id=project.id,
                resource_type="api_key",
                resource_id=key.id,
                metadata={"event": "updated", **changes},
            )
        return key

    async def _validate_profile(
        self, project: Project, profile_id: str | None, scopes: list[str]
    ) -> str | None:
        if not profile_id:
            return None
        profile = await AgentProfileRepository(self.session).get(profile_id, project.id)
        if profile is None:
            raise ValidationError(f"Agent profile '{profile_id}' not found in this project.")
        if ApiKeyScope.APPROVALS_DECIDE.value in scopes:
            raise ValidationError(
                "A key that acts as an agent cannot also decide approvals — it could approve "
                "its own requests. Use a separate key for whoever reviews them."
            )
        return profile.id

    @staticmethod
    def _validate_scopes(scopes: list[str]) -> list[str]:
        allowed = {scope.value for scope in ApiKeyScope}
        cleaned = [scope.strip() for scope in scopes if scope.strip()]
        unknown = [scope for scope in cleaned if scope not in allowed]
        if unknown:
            raise ValidationError(
                f"Unknown scope(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}."
            )
        if not cleaned:
            raise ValidationError("An API key needs at least one scope.")
        # Deduplicate while preserving the caller's order.
        return list(dict.fromkeys(cleaned))
