"""Server-sent events for the dashboard.

The Event Explorer used to poll on a timer, which meant a five-second wait to watch your
own test event land. This streams instead.

Deliberately built on a short database poll rather than a message bus. The events table is
already indexed on ``(project_id, …)``, the query is bounded by a cursor and a limit, and
one query per second per open dashboard tab is far cheaper than the operational cost of
introducing a broker that everything would then depend on. If that ever stops being true,
the generator is the only thing that changes: the wire format is ordinary SSE.

Auth is the standard ``Authorization`` header — the browser's ``EventSource`` cannot send
headers, so the dashboard reads this with ``fetch`` and a stream reader instead, and the
token never goes near a URL or an access log.

The generator opens its own short-lived session per poll rather than using the request's.
A request-scoped session is torn down when the endpoint returns, which for a streaming
response is *before* the body has been written; and a ten-minute stream should not hold a
pooled connection open for ten minutes to do one query a second.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.dependencies import Clearance, UserProject
from app.services.reader import Reader
from app.services.serializers import event_out
from common.logging import get_logger
from common.time import utcnow
from database.repositories import EventRepository
from database.session import session_scope

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["dashboard"])

POLL_SECONDS = 1.0
# Streams are closed after this long so a forgotten tab cannot hold a connection (and a
# database session) open forever. The browser reconnects, which also re-authenticates.
MAX_STREAM_SECONDS = 600
# A heartbeat keeps proxies from closing an idle connection, and tells the client the
# stream is alive during a quiet period.
HEARTBEAT_SECONDS = 15
BATCH_LIMIT = 100


def _frame(event: str, data: Any) -> str:
    """One SSE frame. ``id`` is deliberately omitted: the cursor lives on the server."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/events/stream", include_in_schema=True, summary="Live event stream (SSE)")
async def stream_events(
    request: Request,
    project: UserProject,
    cleared: Clearance,
    since_seconds: int = Query(
        default=30, ge=0, le=3600, description="Replay this many seconds of history first"
    ),
) -> StreamingResponse:
    """Stream events as they arrive and as the worker finishes with them."""
    project_id = project.id
    cursor = utcnow() - timedelta(seconds=since_seconds)

    async def generator() -> AsyncIterator[str]:
        nonlocal cursor
        started = utcnow()
        last_heartbeat = started

        yield _frame("open", {"project_id": project_id, "since": cursor.isoformat()})

        try:
            while True:
                if await request.is_disconnected():
                    break
                if (utcnow() - started).total_seconds() > MAX_STREAM_SECONDS:
                    yield _frame("close", {"reason": "max_duration"})
                    break

                sink: list[dict[str, Any]] = []
                async with session_scope() as session:
                    cursor = await _drain(
                        EventRepository(session), project_id, cursor, sink, Reader(session, cleared=cleared)
                    )
                for payload in sink:
                    yield _frame("event", payload)

                now = utcnow()
                if not sink and (now - last_heartbeat).total_seconds() >= HEARTBEAT_SECONDS:
                    last_heartbeat = now
                    yield _frame("heartbeat", {"at": now.isoformat()})

                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:  # client went away mid-poll
            raise
        except Exception as exc:  # noqa: BLE001 - a broken stream must not 500 the app
            logger.warning("stream.failed", project_id=project_id, error=str(exc))
            yield _frame("close", {"reason": "error"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers by default, which would hold every frame until the stream ends.
            "X-Accel-Buffering": "no",
        },
    )


async def _drain(
    events: EventRepository,
    project_id: str,
    cursor: datetime,
    sink: list[dict[str, Any]],
    reader: Reader,
) -> datetime:
    """Collect everything newer than the cursor, and return the new cursor."""
    rows = await events.changed_since(project_id=project_id, since=cursor, limit=BATCH_LIMIT)
    mask = await reader.event_mask(project_id, rows)
    for event in rows:
        sink.append(
            {
                **event_out(event, mask).model_dump(mode="json"),
                # What moved: a new arrival, or the worker finishing with it.
                "change": "processed" if event.processed_at else "created",
            }
        )
        latest = max(event.created_at, event.processed_at or event.created_at)
        cursor = max(cursor, latest)
    return cursor
