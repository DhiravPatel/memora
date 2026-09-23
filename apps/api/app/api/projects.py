"""Project management, API keys, usage and audit trail (dashboard, JWT auth)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUserDep, DBSession, UserProject
from app.schemas.common import Message
from app.schemas.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectSettingsOut,
    ProjectSettingsUpdate,
    ProjectUpdate,
    ProjectWithKey,
)
from app.schemas.usage import AuditLogOut, OverviewOut, QueryLogOut, UsageOut
from app.services.project_service import ProjectService
from app.services.serializers import project_out, query_log_out
from app.services.settings_service import GROUPS, defaults, schema
from app.services.usage_service import UsageService
from common.enums import UserRole
from database.repositories import QueryLogRepository

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectWithKey, status_code=201)
async def create_project(
    payload: ProjectCreate, session: DBSession, current_user: CurrentUserDep
) -> ProjectWithKey:
    current_user.require(UserRole.ADMIN)
    return await ProjectService(session).create(
        organization_id=current_user.organization_id,
        actor_id=current_user.user.id,
        name=payload.name,
        settings=payload.settings,
    )


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: DBSession, current_user: CurrentUserDep) -> list[ProjectOut]:
    return await ProjectService(session).list(current_user.organization_id)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project: UserProject) -> ProjectOut:
    return project_out(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    payload: ProjectUpdate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> ProjectOut:
    current_user.require(UserRole.ADMIN)
    return await ProjectService(session).update(
        project=project,
        actor_id=current_user.user.id,
        name=payload.name,
        settings=payload.settings,
    )


@router.get("/{project_id}/settings", response_model=ProjectSettingsOut)
async def get_project_settings(
    project: UserProject, session: DBSession
) -> ProjectSettingsOut:
    """Effective settings plus the schema the dashboard form renders from."""
    values = await ProjectService(session).settings(project)
    return ProjectSettingsOut(
        project_id=project.id,
        values=values,
        defaults=defaults(),
        groups=[{"key": key, "label": label} for key, label in GROUPS],
        fields=[field.as_dict() for field in schema()],
    )


@router.put("/{project_id}/settings", response_model=ProjectSettingsOut)
async def update_project_settings(
    payload: ProjectSettingsUpdate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> ProjectSettingsOut:
    """Validate and persist a settings patch. Out-of-range values are refused."""
    current_user.require(UserRole.ADMIN)
    values = await ProjectService(session).replace_settings(
        project=project, settings=payload.settings, actor_id=current_user.user.id
    )
    return ProjectSettingsOut(
        project_id=project.id,
        values=values,
        defaults=defaults(),
        groups=[{"key": key, "label": label} for key, label in GROUPS],
        fields=[field.as_dict() for field in schema()],
    )


@router.post("/{project_id}/rotate-key", response_model=ProjectWithKey)
async def rotate_api_key(
    project: UserProject, session: DBSession, current_user: CurrentUserDep
) -> ProjectWithKey:
    current_user.require(UserRole.ADMIN)
    return await ProjectService(session).rotate_api_key(
        project=project, actor_id=current_user.user.id
    )


@router.delete("/{project_id}", response_model=Message)
async def delete_project(
    project: UserProject, session: DBSession, current_user: CurrentUserDep
) -> Message:
    current_user.require(UserRole.OWNER)
    await ProjectService(session).delete(project=project, actor_id=current_user.user.id)
    return Message(message="Project deleted.")


@router.get("/{project_id}/overview", response_model=OverviewOut)
async def overview(project: UserProject, session: DBSession) -> OverviewOut:
    return await UsageService(session).overview(project)


@router.get("/{project_id}/usage", response_model=UsageOut)
async def usage(
    project: UserProject,
    session: DBSession,
    days: int = Query(default=30, ge=1, le=365),
) -> UsageOut:
    return await UsageService(session).usage(project, days=days)


@router.get("/{project_id}/audit-logs", response_model=list[AuditLogOut])
async def audit_logs(
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditLogOut]:
    current_user.require(UserRole.ADMIN)
    return await UsageService(session).audit_logs(
        organization_id=current_user.organization_id, project_id=project.id, limit=limit
    )


@router.get("/{project_id}/queries", response_model=list[QueryLogOut])
async def query_logs(
    project: UserProject,
    session: DBSession,
    customer_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[QueryLogOut]:
    logs = await QueryLogRepository(session).list(
        project_id=project.id, customer_id=customer_id, limit=limit
    )
    return [query_log_out(log) for log in logs]
