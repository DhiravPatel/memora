"""Project administration: API keys and outbound webhooks (dashboard, JWT auth)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUserDep, DBSession, UserProject
from app.schemas.admin import (
    SCOPE_DESCRIPTIONS,
    WEBHOOK_EVENT_DESCRIPTIONS,
    ApiKeyCreate,
    ApiKeyOut,
    ApiKeyScopesUpdate,
    ApiKeyWithSecret,
    ScopeInfo,
    WebhookDeliveryOut,
    WebhookEndpointCreate,
    WebhookEndpointOut,
    WebhookEndpointUpdate,
    WebhookEndpointWithSecret,
    WebhookEventInfo,
)
from app.schemas.common import Message, Page
from app.services.api_key_service import ApiKeyService
from app.services.serializers import api_key_out, webhook_delivery_out, webhook_endpoint_out
from app.services.webhook_service import WebhookService
from common.enums import DeliveryStatus, UserRole

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["administration"])


# ------------------------------------------------------------------- API keys


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    project: UserProject,
    session: DBSession,
    include_revoked: bool = Query(default=False),
) -> list[ApiKeyOut]:
    keys = await ApiKeyService(session).list(project, include_revoked=include_revoked)
    return [api_key_out(key) for key in keys]


@router.post("/api-keys", response_model=ApiKeyWithSecret, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> ApiKeyWithSecret:
    """Create a scoped key. The raw value is returned exactly once."""
    current_user.require(UserRole.ADMIN)
    created = await ApiKeyService(session).create(
        project=project,
        name=payload.name,
        scopes=[scope.value for scope in payload.scopes] if payload.scopes else None,
        expires_in_days=payload.expires_in_days,
        actor_id=current_user.user.id,
    )
    return ApiKeyWithSecret(
        **api_key_out(created.key).model_dump(), api_key=created.plaintext
    )


@router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
async def update_api_key_scopes(
    key_id: str,
    payload: ApiKeyScopesUpdate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> ApiKeyOut:
    current_user.require(UserRole.ADMIN)
    key = await ApiKeyService(session).update_scopes(
        project=project,
        key_id=key_id,
        scopes=[scope.value for scope in payload.scopes],
        actor_id=current_user.user.id,
    )
    return api_key_out(key)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> ApiKeyOut:
    """Revoke a key. It stops working immediately and stays in the audit trail."""
    current_user.require(UserRole.ADMIN)
    key = await ApiKeyService(session).revoke(
        project=project, key_id=key_id, actor_id=current_user.user.id
    )
    return api_key_out(key)


@router.get("/api-keys/scopes", response_model=list[ScopeInfo])
async def list_scopes(project: UserProject) -> list[ScopeInfo]:
    return [
        ScopeInfo(scope=scope, description=description)
        for scope, description in SCOPE_DESCRIPTIONS.items()
    ]


# ------------------------------------------------------------------- webhooks


@router.get("/webhooks", response_model=list[WebhookEndpointOut])
async def list_webhooks(project: UserProject, session: DBSession) -> list[WebhookEndpointOut]:
    endpoints = await WebhookService(session).list(project)
    return [webhook_endpoint_out(endpoint) for endpoint in endpoints]


@router.post("/webhooks", response_model=WebhookEndpointWithSecret, status_code=201)
async def create_webhook(
    payload: WebhookEndpointCreate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> WebhookEndpointWithSecret:
    """Register an endpoint. The signing secret is shown once."""
    current_user.require(UserRole.ADMIN)
    created = await WebhookService(session).create(
        project=project,
        url=str(payload.url),
        description=payload.description,
        event_types=[event.value for event in payload.event_types],
        actor_id=current_user.user.id,
    )
    return WebhookEndpointWithSecret(
        **webhook_endpoint_out(created.endpoint).model_dump(), secret=created.secret
    )


@router.patch("/webhooks/{endpoint_id}", response_model=WebhookEndpointOut)
async def update_webhook(
    endpoint_id: str,
    payload: WebhookEndpointUpdate,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> WebhookEndpointOut:
    current_user.require(UserRole.ADMIN)
    endpoint = await WebhookService(session).update(
        project=project,
        endpoint_id=endpoint_id,
        url=str(payload.url) if payload.url else None,
        description=payload.description,
        event_types=[event.value for event in payload.event_types]
        if payload.event_types is not None
        else None,
        is_active=payload.is_active,
        actor_id=current_user.user.id,
    )
    return webhook_endpoint_out(endpoint)


@router.post("/webhooks/{endpoint_id}/rotate-secret", response_model=WebhookEndpointWithSecret)
async def rotate_webhook_secret(
    endpoint_id: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> WebhookEndpointWithSecret:
    current_user.require(UserRole.ADMIN)
    rotated = await WebhookService(session).rotate_secret(
        project=project, endpoint_id=endpoint_id, actor_id=current_user.user.id
    )
    return WebhookEndpointWithSecret(
        **webhook_endpoint_out(rotated.endpoint).model_dump(), secret=rotated.secret
    )


@router.post("/webhooks/{endpoint_id}/test", response_model=Message, status_code=202)
async def test_webhook(
    endpoint_id: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> Message:
    """Queue a signed sample payload so a receiver can verify its integration."""
    current_user.require(UserRole.MEMBER)
    delivery_id = await WebhookService(session).send_test(
        project=project, endpoint_id=endpoint_id
    )
    return Message(message=f"Test delivery {delivery_id} queued.")


@router.delete("/webhooks/{endpoint_id}", response_model=Message)
async def delete_webhook(
    endpoint_id: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> Message:
    current_user.require(UserRole.ADMIN)
    await WebhookService(session).delete(
        project=project, endpoint_id=endpoint_id, actor_id=current_user.user.id
    )
    return Message(message="Webhook endpoint deleted.")


@router.get("/webhooks/deliveries", response_model=Page[WebhookDeliveryOut])
async def list_deliveries(
    project: UserProject,
    session: DBSession,
    endpoint_id: str | None = None,
    status: DeliveryStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[WebhookDeliveryOut]:
    deliveries, total = await WebhookService(session).list_deliveries(
        project=project, endpoint_id=endpoint_id, status=status, limit=limit, offset=offset
    )
    return Page[WebhookDeliveryOut](
        data=[webhook_delivery_out(delivery) for delivery in deliveries],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/webhooks/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryOut)
async def retry_delivery(
    delivery_id: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> WebhookDeliveryOut:
    current_user.require(UserRole.MEMBER)
    delivery = await WebhookService(session).retry_delivery(
        project=project, delivery_id=delivery_id
    )
    return webhook_delivery_out(delivery)


@router.get("/webhooks/events", response_model=list[WebhookEventInfo])
async def list_webhook_events(project: UserProject) -> list[WebhookEventInfo]:
    return [
        WebhookEventInfo(event=event, description=description)
        for event, description in WEBHOOK_EVENT_DESCRIPTIONS.items()
    ]
