"""Memory contracts over the API (§26 7.1).

Reading a contract, testing a payload against it and drafting one from traffic need only
``events:write`` — the key an ingestion pipeline holds, which is exactly the code that should
check its payloads in CI. Saving and deleting change how every event of the type is
received, so they need ``admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.dependencies import ApiProject, DBSession, require_scope
from app.schemas.contracts import (
    ContractCoverageOut,
    ContractIn,
    ContractOut,
    ContractTestIn,
    ContractTestOut,
)
from app.services.contract_service import ContractService
from common.enums import ApiKeyScope

router = APIRouter(prefix="/v1/contracts", tags=["contracts"])
ADMIN = [Depends(require_scope(ApiKeyScope.ADMIN))]
SINCE = Query(default=None, max_length=8, description="How far back reports read: 24h, 7d (default), 30d")


def _actor(request: Request) -> str | None:
    key = getattr(request.state, "api_key", None)
    return getattr(key, "id", None)


@router.get("", response_model=list[ContractOut])
async def list_contracts(project: ApiProject, session: DBSession, since: str | None = SINCE) -> list[ContractOut]:
    """Every contract, with how many events of its type arrived and broke it in the window."""
    return [ContractOut(**row) for row in await ContractService(session).list(project=project, since=since)]


@router.get("/coverage", response_model=list[ContractCoverageOut])
async def contract_coverage(project: ApiProject, session: DBSession, since: str | None = SINCE) -> list[ContractCoverageOut]:
    """Every event type received in the window — and whether a contract covers it."""
    return [ContractCoverageOut(**row) for row in await ContractService(session).coverage(project=project, since=since)]


@router.post("", response_model=ContractOut, status_code=status.HTTP_201_CREATED, dependencies=ADMIN)
async def save_contract(
    payload: ContractIn, request: Request, response: Response, project: ApiProject, session: DBSession
) -> ContractOut:
    """Create a contract — or replace the one for the same event type (its version moves).
    Send the fields as an object, or the whole contract as YAML in `yaml`."""
    saved = await ContractService(session).save(
        project=project, raw=payload.raw(), yaml_text=payload.yaml, actor_type="api_key", actor_id=_actor(request)
    )
    if not saved.pop("created"):
        response.status_code = status.HTTP_200_OK
    return ContractOut(**saved)


@router.get("/{event_type}", response_model=ContractOut)
async def get_contract(event_type: str, project: ApiProject, session: DBSession, since: str | None = SINCE) -> ContractOut:
    """The contract, and what it found: events checked, events breaking it, and each way they
    broke it — expected, received, how often, when last."""
    return ContractOut(**await ContractService(session).get(project=project, event_type=event_type, since=since))


@router.put("/{event_type}", response_model=ContractOut, dependencies=ADMIN)
async def replace_contract(
    event_type: str, payload: ContractIn, request: Request, project: ApiProject, session: DBSession
) -> ContractOut:
    return ContractOut(
        **await ContractService(session).save(
            project=project,
            raw=payload.raw(),
            yaml_text=payload.yaml,
            event_type=event_type.strip().lower(),
            actor_type="api_key",
            actor_id=_actor(request),
        )
    )


@router.delete("/{event_type}", status_code=status.HTTP_204_NO_CONTENT, dependencies=ADMIN)
async def delete_contract(event_type: str, request: Request, project: ApiProject, session: DBSession) -> Response:
    await ContractService(session).delete(project=project, event_type=event_type, actor_type="api_key", actor_id=_actor(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{event_type}/test", response_model=ContractTestOut)
async def test_contract(event_type: str, payload: ContractTestIn, project: ApiProject, session: DBSession) -> ContractTestOut:
    """Check a payload against the contract — or against a proposed one — without sending it.
    Made for CI: run your fixtures through it before deploying a change."""
    return ContractTestOut(
        **await ContractService(session).test(
            project=project,
            event_type=event_type.strip().lower(),
            data=payload.data,
            raw=payload.contract,
            yaml_text=payload.yaml,
        )
    )


@router.post("/{event_type}/draft", response_model=dict)
async def draft_contract(
    event_type: str,
    project: ApiProject,
    session: DBSession,
    limit: int = Query(default=200, ge=1, le=1000, description="How many recent events to learn from"),
) -> dict:
    """A contract inferred from the type's recent payloads: required fields, types, enums and
    the text field — a draft to read, adjust and save."""
    return await ContractService(session).draft(project=project, event_type=event_type.strip().lower(), limit=limit)
