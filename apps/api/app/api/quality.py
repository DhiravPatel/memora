"""The memory quality report (project API key)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.dependencies import ApiProject, Clearance, DBSession
from app.schemas.quality import QualityReport
from app.services.quality_service import QualityService

router = APIRouter(prefix="/v1/quality", tags=["quality"])


@router.get("", response_model=QualityReport)
async def quality_report(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    days: int = Query(default=30, ge=1, le=365),
) -> QualityReport:
    """How good this project's memory is, and — for every part that is not — why and what to change."""
    return QualityReport(
        **await QualityService(session, cleared=cleared).report(project=project, days=days)
    )
