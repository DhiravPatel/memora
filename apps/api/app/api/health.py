"""Liveness, readiness and metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.dependencies import DBSession
from app.core.queue import queue_depth
from common.metrics import queue_depth as queue_depth_gauge
from common.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": get_settings().app_env}


@router.get("/ready", summary="Readiness probe")
async def ready(session: DBSession) -> dict[str, Any]:
    checks: dict[str, Any] = {"database": "ok", "queue": "ok"}
    status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
        status = "degraded"

    try:
        depth = await queue_depth()
        queue_depth_gauge.set(depth)
        checks["queue_depth"] = depth
    except Exception as exc:  # noqa: BLE001
        checks["queue"] = f"error: {exc}"
        status = "degraded"

    return {"status": status, "checks": checks}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
