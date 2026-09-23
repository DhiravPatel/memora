"""Goals (project API key): what customers said they were trying to do, and where it got to."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import ApiProject, DBSession, require_scope
from app.schemas.common import Page
from app.schemas.goals import GoalOut, GoalSummaryOut, GoalUpdate
from app.services.goal_service import GoalService
from app.services.serializers import goal_out
from common.enums import ApiKeyScope, GoalStatus

WRITE = [Depends(require_scope(ApiKeyScope.MEMORY_WRITE))]

router = APIRouter(prefix="/v1/goals", tags=["goals"])


@router.get("", response_model=Page[GoalOut])
async def list_goals(
    project: ApiProject,
    session: DBSession,
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[GoalOut]:
    """Every tracked goal in the project, live ones first."""
    goals, total = await GoalService(session).list_for_project(
        project=project, status=status, limit=limit, offset=offset
    )
    return Page[GoalOut](
        data=[goal_out(goal) for goal in goals], total=total, limit=limit, offset=offset
    )


@router.get("/summary", response_model=GoalSummaryOut)
async def goal_summary(project: ApiProject, session: DBSession) -> GoalSummaryOut:
    counts = await GoalService(session).counts(project=project)
    return GoalSummaryOut(**counts.as_dict())


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(goal_id: str, project: ApiProject, session: DBSession) -> GoalOut:
    return goal_out(await GoalService(session).get(project=project, goal_id=goal_id))


@router.patch("/{goal_id}", response_model=GoalOut, dependencies=WRITE)
async def update_goal(
    goal_id: str, payload: GoalUpdate, project: ApiProject, session: DBSession
) -> GoalOut:
    """Set a goal's status by hand.

    The tracker stops touching an overridden goal, so a correction sticks rather than
    being undone by the next event.
    """
    goal = await GoalService(session).override(
        project=project,
        goal_id=goal_id,
        status=GoalStatus(payload.status),
        note=payload.note,
        actor_type="api_key",
        actor_id=project.id,
    )
    return goal_out(goal)
