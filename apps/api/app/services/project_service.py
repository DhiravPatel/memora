"""Project lifecycle and API key management."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import enqueue
from app.core.security import api_key_prefix, generate_api_key, hash_api_key
from app.schemas.projects import ProjectOut, ProjectWithKey
from app.services.serializers import project_out
from app.services.settings_service import effective, new_project_settings
from app.services.settings_service import validate as validate_settings
from common.enums import ApiKeyScope, AuditAction
from common.errors import ConflictError, NotFoundError
from common.logging import get_logger
from common.settings import get_settings
from database.models import Project
from database.repositories import ApiKeyRepository, AuditRepository, ProjectRepository

logger = get_logger(__name__)


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.keys = ApiKeyRepository(session)
        self.audit = AuditRepository(session)

    async def create(
        self,
        *,
        organization_id: str,
        actor_id: str,
        name: str,
        settings: dict[str, Any] | None = None,
    ) -> ProjectWithKey:
        existing = await self.projects.list_for_organization(organization_id)
        if any(project.name.lower() == name.strip().lower() for project in existing):
            raise ConflictError(f"A project named '{name}' already exists.")

        environment = "test" if get_settings().app_env != "production" else "live"
        api_key = generate_api_key(environment)
        project = await self.projects.create(
            organization_id=organization_id,
            name=name.strip(),
            api_key_hash=hash_api_key(api_key),
            api_key_prefix=api_key_prefix(api_key),
            # New projects start with the engagement and commercial tracks (§26 4.2).
            settings=new_project_settings(settings),
        )
        # The first key is a real, listable, revocable key rather than a hidden column.
        await self.keys.create(
            project_id=project.id,
            name="Default key",
            key_hash=hash_api_key(api_key),
            key_prefix=api_key_prefix(api_key),
            scopes=[ApiKeyScope.ADMIN.value],
            created_by=actor_id,
            metadata={"created_with_project": True},
        )
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type="user",
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"event": "project_created"},
        )
        return ProjectWithKey(**project_out(project).model_dump(), api_key=api_key)

    async def list(self, organization_id: str) -> list[ProjectOut]:
        return [project_out(project) for project in await self.projects.list_for_organization(organization_id)]

    async def get(self, project_id: str, organization_id: str) -> ProjectOut:
        project = await self.projects.get_for_organization(project_id, organization_id)
        if project is None:
            raise NotFoundError("Project not found.")
        return project_out(project)

    async def update(
        self,
        *,
        project: Project,
        actor_id: str,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> ProjectOut:
        if name and name.strip() != project.name:
            await self._assert_unique_name(project, name)
            project.name = name.strip()
        previous_policies = (project.settings or {}).get("restriction_policies")
        previous_lifecycle = (project.settings or {}).get("lifecycle")
        if settings is not None:
            # Every write goes through the same validation the settings form renders from,
            # so an out-of-range value can never reach the engine.
            validated = validate_settings(settings)
            await self.projects.update_settings(project, validated)
            await self._reclassify_if_policy_changed(project, previous_policies, validated)
            await self._reevaluate_if_lifecycle_changed(project, previous_lifecycle, validated)
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type="user",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"event": "project_updated", "fields": {"name": bool(name), "settings": settings is not None}},
        )
        return project_out(project)

    async def settings(self, project: Project) -> dict[str, Any]:
        """Stored settings layered over the deployment defaults."""
        return effective(project)

    async def replace_settings(
        self, *, project: Project, settings: dict[str, Any], actor_id: str | None = None
    ) -> dict[str, Any]:
        """Validate and persist a settings patch, returning the effective result."""
        previous = (project.settings or {}).get("restriction_policies")
        previous_lifecycle = (project.settings or {}).get("lifecycle")
        validated = validate_settings(settings)
        await self.projects.update_settings(project, validated)
        await self._reclassify_if_policy_changed(project, previous, validated)
        await self._reevaluate_if_lifecycle_changed(project, previous_lifecycle, validated)
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project_settings",
            resource_id=project.id,
            metadata={"event": "settings_updated", "keys": sorted(validated)},
        )
        return effective(project)

    async def _reclassify_if_policy_changed(
        self, project: Project, previous: Any, patch: dict[str, Any]
    ) -> None:
        """Re-apply the restriction policy to memories written before it changed.

        Without this, turning a rule on would gate new memories and leave every existing
        one readable — the exact leak the rule was added to close. The work happens in the
        worker because a large project would otherwise hang this request.
        """
        if "restriction_policies" not in patch:
            return
        if patch["restriction_policies"] == (previous or []):
            return
        queued = await enqueue("reclassify_memories", project.id)
        logger.info(
            "policy.reclassify_queued",
            project_id=project.id,
            rules=len(patch["restriction_policies"] or []),
            queued=queued is not None,
        )

    async def _reevaluate_if_lifecycle_changed(
        self, project: Project, previous: Any, patch: dict[str, Any]
    ) -> None:
        """Move every customer under the new rules now, rather than one event at a time.

        A changed machine applied lazily would leave quiet customers in states the new
        rules would never have put them in — possibly states that no longer exist.
        """
        if "lifecycle" not in patch or patch["lifecycle"] == previous:
            return
        queued = await enqueue("refresh_customer_states", project.id, "lifecycle_changed")
        logger.info("lifecycle.reevaluate_queued", project_id=project.id, queued=queued is not None)

    async def _assert_unique_name(self, project: Project, name: str) -> None:
        siblings = await self.projects.list_for_organization(project.organization_id)
        if any(
            other.id != project.id and other.name.lower() == name.strip().lower()
            for other in siblings
        ):
            raise ConflictError(f"A project named '{name.strip()}' already exists.")

    async def rotate_api_key(self, *, project: Project, actor_id: str) -> ProjectWithKey:
        """Issue a new key. The previous key stops working immediately."""
        environment = "test" if get_settings().app_env != "production" else "live"
        api_key = generate_api_key(environment)
        digest = hash_api_key(api_key)

        # Retire the key rows that mirrored the project's own key, then issue a new one.
        for key in await self.keys.list(project.id):
            if (key.meta or {}).get("created_with_project") or key.key_hash == project.api_key_hash:
                await self.keys.revoke(key)
        await self.projects.rotate_api_key(
            project, api_key_hash=digest, prefix=api_key_prefix(api_key)
        )
        await self.keys.create(
            project_id=project.id,
            name="Default key",
            key_hash=digest,
            key_prefix=api_key_prefix(api_key),
            scopes=[ApiKeyScope.ADMIN.value],
            created_by=actor_id,
            metadata={"created_with_project": True},
        )
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type="user",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"event": "api_key_rotated"},
        )
        return ProjectWithKey(**project_out(project).model_dump(), api_key=api_key)

    async def delete(self, *, project: Project, actor_id: str) -> None:
        await self.audit.record(
            action=AuditAction.DATA_DELETION,
            actor_type="user",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"event": "project_deleted"},
        )
        await self.projects.delete(project)
