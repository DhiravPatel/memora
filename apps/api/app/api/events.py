"""Event ingestion API (project API key)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.core import idempotency
from app.core.dependencies import ApiProject, Clearance, DBSession, Engine, require_scope
from app.core.ratelimit import check_quota
from app.schemas.common import Page
from app.schemas.events import (
    EventAccepted,
    EventBatchAccepted,
    EventBatchIn,
    EventExplanationOut,
    EventIn,
    EventOut,
    EventPreviewIn,
)
from app.services.event_service import EventService
from app.services.serializers import event_out
from common.enums import ApiKeyScope, EventStatus
from common.errors import ConflictError, NotFoundError
from database.repositories import CustomerRepository, EventRepository, UsageRepository

router = APIRouter(prefix="/v1/events", tags=["events"])


async def _check_quota(session: DBSession, project: ApiProject, cost: int = 1) -> None:
    """Monthly allowance, read from the project's own usage counters."""
    first_of_month = date.today().replace(day=1)
    totals = await UsageRepository(session).totals(project_id=project.id, since=first_of_month)
    await check_quota(project, used_this_month=totals.get("events_received", 0) + cost - 1)


@router.post("", response_model=EventAccepted, status_code=status.HTTP_202_ACCEPTED)
async def track_event(
    payload: EventIn,
    project: ApiProject,
    session: DBSession,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EventAccepted:
    """Accept an event, store it immutably and queue it for memory extraction.

    Safe to retry: the same ``Idempotency-Key`` replays the original response, and the same
    ``external_event_id`` is only ever stored once.
    """
    await _check_quota(session, project)

    body = payload.model_dump(mode="json")
    if idempotency_key:
        stored = await idempotency.lookup(
            project_id=project.id, path=request.url.path, key=idempotency_key
        )
        if stored is not None:
            if stored.fingerprint and stored.fingerprint != idempotency.fingerprint(body):
                raise ConflictError(
                    "This Idempotency-Key was already used with a different request body."
                )
            response.status_code = stored.status_code
            response.headers["Idempotent-Replay"] = "true"
            return EventAccepted(**stored.body)

    result = await EventService(session).ingest(project=project, payload=payload)
    if result.status == "duplicate":
        response.status_code = status.HTTP_200_OK

    if idempotency_key:
        await idempotency.remember(
            project_id=project.id,
            path=request.url.path,
            key=idempotency_key,
            status_code=response.status_code or status.HTTP_202_ACCEPTED,
            body=result.model_dump(mode="json"),
            request_fingerprint=idempotency.fingerprint(body),
        )
    return result


@router.post("/batch", response_model=EventBatchAccepted, status_code=status.HTTP_202_ACCEPTED)
async def track_events(
    payload: EventBatchIn, project: ApiProject, session: DBSession
) -> EventBatchAccepted:
    await _check_quota(session, project, cost=len(payload.events))
    accepted, duplicates = await EventService(session).ingest_batch(
        project=project, payloads=payload.events
    )
    return EventBatchAccepted(accepted=accepted, duplicates=duplicates)


@router.post(
    "/preview",
    response_model=EventExplanationOut,
    # The router already requires events:write. Previewing also *reads* existing memories
    # to decide what a candidate would merge into, so it needs memory:read too — otherwise
    # an ingestion-only key could read memory content back out of a preview response.
    dependencies=[Depends(require_scope(ApiKeyScope.MEMORY_READ))],
)
async def preview_event(
    payload: EventPreviewIn,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
    cleared: Clearance,
) -> EventExplanationOut:
    """What this event *would* do. Writes nothing.

    Every decision comes from the same code the real pipeline runs, so this is not a
    simulation — it is the pipeline, stopped before it commits. Use it to find out why an
    event produces no memory before spending a day wondering, and to see what an extracted
    memory would merge into or contradict.

    Reads existing memories to decide, so it needs ``memory:read``. A preview of a
    restricted memory needs clearance like any other read; without it the preview reports
    the memory as restricted but does not show what it would have merged into.
    """
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")

    explanation = await engine.preview_event(
        project=project,
        customer=customer,
        event_type=payload.event_type,
        data=payload.data,
        occurred_at=payload.occurred_at,
    )
    return EventExplanationOut(**explanation.as_dict(), duration_ms=explanation.duration_ms)


@router.get("", response_model=Page[EventOut])
async def list_events(
    project: ApiProject,
    session: DBSession,
    customer_id: str | None = None,
    event_type: str | None = None,
    event_status: EventStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[EventOut]:
    resolved_customer_id = None
    if customer_id:
        customer = await CustomerRepository(session).resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
        resolved_customer_id = customer.id

    events, total = await EventRepository(session).list(
        project_id=project.id,
        customer_id=resolved_customer_id,
        event_type=event_type,
        status=event_status,
        limit=limit,
        offset=offset,
    )
    return Page[EventOut](
        data=[event_out(event) for event in events], total=total, limit=limit, offset=offset
    )


@router.post("/{event_id}/retry", response_model=EventAccepted, status_code=status.HTTP_202_ACCEPTED)
async def retry_event(event_id: str, project: ApiProject, session: DBSession) -> EventAccepted:
    """Re-queue a failed event. Events are immutable, so replaying one is always safe."""
    return await EventService(session).retry(project=project, event_id=event_id)


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: str, project: ApiProject, session: DBSession) -> EventOut:
    event = await EventRepository(session).get(event_id, project.id)
    if event is None:
        raise NotFoundError("Event not found.")
    return event_out(event)
