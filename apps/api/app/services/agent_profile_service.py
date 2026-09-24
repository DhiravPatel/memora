"""Agent profiles: what each agent is for (§26 3.1).

A profile narrows a key; it never widens one. Memory types it may not read are filtered in
SQL by every reader-bound repository, restricted memories need the key's clearance *and*
the profile's consent, and its action lists are the first layer of every guardrail check.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent_policy import AgentProfileIn, AgentProfileOut, AgentProfileUpdate
from common.enums import AuditAction
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from database.models import AgentProfile, Project
from database.repositories import AgentProfileRepository, AuditRepository

logger = get_logger(__name__)

MAX_PROFILES = 50


def profile_out(profile: AgentProfile, keys: int = 0) -> AgentProfileOut:
    return AgentProfileOut(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        readable_types=list(profile.readable_types or []),
        can_read_restricted=bool(profile.can_read_restricted),
        allowed_actions=list(profile.allowed_actions or []),
        denied_actions=list(profile.denied_actions or []),
        keys=keys,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _check_actions(allowed: list[str], denied: list[str]) -> None:
    both = sorted(set(allowed) & set(denied))
    if both:
        raise ValidationError(f"An action cannot be both allowed and denied: {', '.join(both)}.")


class AgentProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.profiles = AgentProfileRepository(session)
        self.audit = AuditRepository(session)

    async def list(self, project: Project) -> list[AgentProfileOut]:
        counts = await self.profiles.key_counts(project.id)
        return [profile_out(row, counts.get(row.id, 0)) for row in await self.profiles.list(project.id)]

    async def get(self, project: Project, profile_id: str) -> AgentProfileOut:
        profile = await self._get(project, profile_id)
        counts = await self.profiles.key_counts(project.id)
        return profile_out(profile, counts.get(profile.id, 0))

    async def create(
        self, *, project: Project, payload: AgentProfileIn, actor_type: str, actor_id: str | None
    ) -> AgentProfileOut:
        if len(await self.profiles.list(project.id)) >= MAX_PROFILES:
            raise ConflictError(f"A project holds at most {MAX_PROFILES} agent profiles.")
        if await self.profiles.get_by_name(project.id, payload.name) is not None:
            raise ConflictError(f"An agent profile named '{payload.name}' already exists.")
        _check_actions(payload.allowed_actions, payload.denied_actions)
        try:
            profile = await self.profiles.create(
                project_id=project.id,
                name=payload.name,
                description=(payload.description or "").strip() or None,
                readable_types=[item.value for item in payload.readable_types],
                can_read_restricted=payload.can_read_restricted,
                allowed_actions=payload.allowed_actions,
                denied_actions=payload.denied_actions,
            )
        except IntegrityError as exc:  # a concurrent create with the same name
            raise ConflictError(f"An agent profile named '{payload.name}' already exists.") from exc
        await self._audit(project, profile, "created", actor_type, actor_id)
        return profile_out(profile)

    async def update(
        self,
        *,
        project: Project,
        profile_id: str,
        payload: AgentProfileUpdate,
        actor_type: str,
        actor_id: str | None,
    ) -> AgentProfileOut:
        profile = await self._get(project, profile_id)
        fields: dict[str, object] = {}
        given = payload.model_fields_set
        if "description" in given:
            fields["description"] = (payload.description or "").strip() or None
        if "readable_types" in given and payload.readable_types is not None:
            fields["readable_types"] = [item.value for item in payload.readable_types]
        if "can_read_restricted" in given and payload.can_read_restricted is not None:
            fields["can_read_restricted"] = payload.can_read_restricted
        if "allowed_actions" in given and payload.allowed_actions is not None:
            fields["allowed_actions"] = payload.allowed_actions
        if "denied_actions" in given and payload.denied_actions is not None:
            fields["denied_actions"] = payload.denied_actions
        _check_actions(
            list(fields.get("allowed_actions", profile.allowed_actions) or []),  # type: ignore[arg-type]
            list(fields.get("denied_actions", profile.denied_actions) or []),  # type: ignore[arg-type]
        )
        if fields:
            await self.profiles.update(profile, **fields)
            await self._audit(project, profile, "updated", actor_type, actor_id, sorted(fields))
        counts = await self.profiles.key_counts(project.id)
        return profile_out(profile, counts.get(profile.id, 0))

    async def delete(
        self, *, project: Project, profile_id: str, actor_type: str, actor_id: str | None
    ) -> None:
        profile = await self._get(project, profile_id)
        bound = (await self.profiles.key_counts(project.id)).get(profile.id, 0)
        if bound:
            # Deleting would silently *widen* those keys to everything their scopes allow.
            raise ConflictError(
                f"{bound} active key{'s act' if bound != 1 else ' acts'} as this profile. "
                "Rebind or revoke them first — deleting it would widen what they can read."
            )
        await self.profiles.delete(profile)
        await self._audit(project, profile, "deleted", actor_type, actor_id)

    async def _get(self, project: Project, profile_id: str) -> AgentProfile:
        profile = await self.profiles.get(profile_id, project.id)
        if profile is None:
            raise NotFoundError(f"Agent profile '{profile_id}' not found.")
        return profile

    async def _audit(
        self,
        project: Project,
        profile: AgentProfile,
        event: str,
        actor_type: str,
        actor_id: str | None,
        changed: list[str] | None = None,
    ) -> None:
        await self.audit.record(
            project_id=project.id,
            organization_id=project.organization_id,
            action=AuditAction.AGENT_POLICY_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="agent_profile",
            resource_id=profile.id,
            metadata={"event": event, "name": profile.name, **({"changed": changed} if changed else {})},
        )
        logger.info("agent_profile." + event, project_id=project.id, profile_id=profile.id)
