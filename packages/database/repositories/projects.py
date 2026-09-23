"""Projects and their hashed API keys."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.ids import new_id
from database.models import DEFAULT_PROJECT_SETTINGS, Project
from database.repositories.base import BaseRepository


class ProjectRepository(BaseRepository):
    async def create(
        self,
        *,
        organization_id: str,
        name: str,
        api_key_hash: str,
        api_key_prefix: str,
        settings: dict[str, Any] | None = None,
    ) -> Project:
        project = Project(
            id=new_id("prj"),
            organization_id=organization_id,
            name=name,
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            settings={**DEFAULT_PROJECT_SETTINGS, **(settings or {})},
        )
        self.session.add(project)
        await self.session.flush()
        return project

    async def get(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def get_for_organization(self, project_id: str, organization_id: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id, Project.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_api_key_hash(self, api_key_hash: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.api_key_hash == api_key_hash, Project.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def list_for_organization(self, organization_id: str) -> list[Project]:
        result = await self.session.execute(
            select(Project)
            .where(Project.organization_id == organization_id)
            .order_by(Project.created_at.desc())
        )
        return list(result.scalars())

    async def rotate_api_key(self, project: Project, *, api_key_hash: str, prefix: str) -> Project:
        project.api_key_hash = api_key_hash
        project.api_key_prefix = prefix
        await self.session.flush()
        return project

    async def update_settings(self, project: Project, settings: dict[str, Any]) -> Project:
        project.settings = {**(project.settings or {}), **settings}
        await self.session.flush()
        return project

    async def delete(self, project: Project) -> None:
        await self.session.delete(project)
