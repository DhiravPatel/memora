"""Personalization for a product's own code (§26 6.6).

Mounted on its own router so a key with only ``personalization:read`` — the narrow key a
product's backend holds to adapt its UI — can read it; ``customers:read`` and
``memory:read`` keys can too.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from app.core.dependencies import ApiCustomer, ApiProject, Clearance, DBSession
from app.schemas.personalization import (
    PersonalizationBatchIn,
    PersonalizationBatchOut,
    PersonalizationOut,
    PersonalizationRulesOut,
)
from app.services.personalization_service import PersonalizationService, rules_for
from memory_engine.personalization import describe

router = APIRouter(tags=["personalization"])

# Fresh enough to cache between page loads; a webhook says when it changes.
CACHE_SECONDS = 30


def _matches(request: Request, etag: str) -> bool:
    wanted = request.headers.get("if-none-match") or ""
    return any(tag.strip() in (etag, "*") for tag in wanted.split(","))


@router.get(
    "/v1/customers/{customer_id}/personalization",
    response_model=PersonalizationOut,
    responses={304: {"description": "Unchanged since the version you sent in If-None-Match"}},
)
async def get_personalization(
    request: Request,
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    details: bool = Query(default=True, description="Include why each value is what it is."),
    fresh: bool = Query(default=False, description="Recompute now instead of reading the stored document."),
) -> Response:
    """What your product should do differently for this customer: experience level, mood,
    preferred channel and opt-outs, current goal, known frictions, features used and relied
    on, stage, plan, health, and UI hints — each with the facts behind it.

    Computed when the customer's events are processed and stored, so a read is fast. Send
    the `ETag` back as `If-None-Match` to get `304` while nothing changed; subscribe to
    `customer.personalization_changed` to hear when it does.
    """
    body = await PersonalizationService(session).for_reader(
        project=project, customer=customer, cleared=cleared, details=details, fresh=fresh
    )
    etag = f'W/"{body["version"]}{"" if details else "-flat"}"'
    headers = {"ETag": etag, "Cache-Control": f"private, max-age={CACHE_SECONDS}"}
    if _matches(request, etag):
        return Response(status_code=304, headers=headers)
    payload = PersonalizationOut(**body).model_dump(mode="json", exclude=None if details else {"details"})
    return JSONResponse(payload, headers=headers)


@router.post("/v1/personalization/batch", response_model=PersonalizationBatchOut)
async def batch_personalization(
    payload: PersonalizationBatchIn, project: ApiProject, session: DBSession, cleared: Clearance
) -> PersonalizationBatchOut:
    """Up to 50 customers at once — for a server rendering a list. An unknown id comes back
    with `error: "not_found"` rather than failing the rest."""
    rows = await PersonalizationService(session).batch(
        project=project, customer_ids=payload.customer_ids, cleared=cleared, details=payload.details
    )
    return PersonalizationBatchOut(data=rows)


@router.get("/v1/personalization/rules", response_model=PersonalizationRulesOut)
async def personalization_rules(project: ApiProject) -> PersonalizationRulesOut:
    """The rules in force — experience levels, moods and hints, the built-ins included."""
    return PersonalizationRulesOut(**describe(rules_for(project)))
