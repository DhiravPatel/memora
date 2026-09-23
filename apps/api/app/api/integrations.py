"""Integration webhooks.

One endpoint per provider, authenticated twice: the project API key identifies the
tenant, and the provider's own signature proves the payload is real. Events are then
ingested through exactly the same path as `POST /v1/events` — connectors are translation,
never a second ingestion pipeline.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status

import integrations
from app.core.dependencies import ApiProject, DBSession
from app.schemas.events import EventBatchAccepted, EventIn
from common.crypto import decrypt
from common.logging import get_logger
from common.settings import get_settings
from database.models import Project

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])


def provider_settings(project: Project, provider: str) -> dict[str, Any]:
    """This provider's configuration, with its signing secret decrypted for use.

    The secret is stored encrypted (see ``settings_service``); verification needs the
    value itself, so it is decrypted here at the point of use and nowhere else.
    """
    configured = (project.settings or {}).get("integrations") or {}
    value = configured.get(provider)
    if not isinstance(value, dict):
        return {}

    secret = value.get("signing_secret")
    if not secret:
        return value
    keys = get_settings().encryption_keys
    if not keys:
        return value
    return {**value, "signing_secret": decrypt(str(secret), keys)}


@router.get("", summary="List available integrations")
async def list_integrations(project: ApiProject) -> dict[str, Any]:
    configured = (project.settings or {}).get("integrations") or {}
    return {
        "providers": [
            {
                **entry,
                "configured": bool(
                    isinstance(configured.get(entry["provider"]), dict)
                    and configured[entry["provider"]].get("signing_secret")
                ),
                "webhook_url": f"/v1/integrations/{entry['provider']}/webhook",
            }
            for entry in integrations.describe()
        ]
    }


@router.post(
    "/{provider}/webhook",
    response_model=EventBatchAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a provider webhook",
)
async def receive_webhook(
    provider: str,
    request: Request,
    project: ApiProject,
    session: DBSession,
) -> EventBatchAccepted:
    from app.services.event_service import EventService

    settings = provider_settings(project, provider.strip().lower())
    raw_body = await request.body()
    payload = await _json_body(request)

    normalized = integrations.process(
        provider=provider,
        payload=payload,
        raw_body=raw_body,
        headers=dict(request.headers),
        secret=settings.get("signing_secret"),
        # Unsigned webhooks are only possible when a project explicitly opts out, which is
        # reasonable for internal systems behind a private network and nowhere else.
        require_signature=not settings.get("allow_unsigned", False),
    )

    if not normalized:
        logger.info("integration.no_events", provider=provider, project_id=project.id)
        return EventBatchAccepted(accepted=[], duplicates=0)

    payloads = [EventIn(**event.as_payload()) for event in normalized]
    accepted, duplicates = await EventService(session).ingest_batch(
        project=project, payloads=payloads
    )
    logger.info(
        "integration.ingested",
        provider=provider,
        project_id=project.id,
        accepted=len(accepted),
        duplicates=duplicates,
    )
    return EventBatchAccepted(accepted=accepted, duplicates=duplicates)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client error, not a crash
        from common.errors import ValidationError

        raise ValidationError("Webhook body must be valid JSON.") from None
    if isinstance(body, list):
        return {"events": body} if body and isinstance(body[0], dict) else {}
    return body if isinstance(body, dict) else {}


__all__ = ["router"]
