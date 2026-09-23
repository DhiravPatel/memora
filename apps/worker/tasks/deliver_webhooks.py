"""Outbound webhook delivery.

Deliveries are rows, so this job is a drain loop: take what is due, POST it with a signed
body, record the outcome, and reschedule with backoff on failure. Nothing here talks to the
request path, so a slow or hostile receiver can never affect ingestion.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from common.enums import DeliveryStatus
from common.logging import get_logger
from database.models import WebhookEndpoint
from database.repositories import (
    MAX_ATTEMPTS,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
)
from webhooks import DELIVERY_HEADER, EVENT_HEADER, SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from worker.tasks.context import worker_session

logger = get_logger(__name__)

TIMEOUT_SECONDS = 10.0
BATCH_SIZE = 100
USER_AGENT = "Memora-Webhooks/1.0"
# Receivers should answer quickly; a huge body is never read anyway.
MAX_RESPONSE_CHARS = 2000


async def deliver_webhooks(ctx: dict[str, Any], limit: int = BATCH_SIZE) -> dict[str, Any]:
    """Send every delivery whose backoff has elapsed."""
    sent = failed = skipped = 0

    async with worker_session() as session:
        deliveries = WebhookDeliveryRepository(session)
        endpoints = WebhookEndpointRepository(session)
        due = await deliveries.due(limit=limit)
        if not due:
            return {"sent": 0, "failed": 0, "skipped": 0}

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,  # a redirected webhook is a misconfigured webhook
        ) as client:
            for delivery in due:
                endpoint = await endpoints.get(delivery.endpoint_id, delivery.project_id)
                if endpoint is None or not endpoint.is_active:
                    delivery.status = DeliveryStatus.DISABLED
                    delivery.error = "Endpoint is inactive or was deleted."
                    skipped += 1
                    continue

                outcome = await _attempt(client, endpoint, delivery.payload)
                if outcome["ok"]:
                    await deliveries.mark_succeeded(
                        delivery,
                        status_code=outcome["status"],
                        body=outcome["body"],
                        duration_ms=outcome["duration_ms"],
                    )
                    await endpoints.record_success(endpoint)
                    sent += 1
                else:
                    will_retry = await deliveries.mark_failed(
                        delivery,
                        error=outcome["error"],
                        status_code=outcome["status"],
                        body=outcome["body"],
                        duration_ms=outcome["duration_ms"],
                    )
                    await endpoints.record_failure(endpoint, outcome["error"])
                    failed += 1
                    logger.warning(
                        "webhook.delivery_failed",
                        delivery_id=delivery.id,
                        endpoint_id=endpoint.id,
                        attempts=delivery.attempts,
                        will_retry=will_retry,
                        error=outcome["error"][:200],
                    )

    if sent or failed:
        logger.info("webhook.batch", sent=sent, failed=failed, skipped=skipped)
    return {"sent": sent, "failed": failed, "skipped": skipped}


async def _attempt(
    client: httpx.AsyncClient, endpoint: WebhookEndpoint, payload: dict[str, Any]
) -> dict[str, Any]:
    body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    signature, timestamp = sign(secret=endpoint.secret, body=body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        SIGNATURE_HEADER: signature,
        TIMESTAMP_HEADER: str(timestamp),
        EVENT_HEADER: str(payload.get("type", "")),
        DELIVERY_HEADER: str(payload.get("id", "")),
    }

    started = time.perf_counter()
    try:
        response = await client.post(endpoint.url, content=body, headers=headers)
    except httpx.TimeoutException:
        return _result(False, None, "", started, f"Timed out after {TIMEOUT_SECONDS}s")
    except httpx.TransportError as exc:
        return _result(False, None, "", started, f"Connection failed: {exc}")

    text = (response.text or "")[:MAX_RESPONSE_CHARS]
    if 200 <= response.status_code < 300:
        return _result(True, response.status_code, text, started, "")
    return _result(
        False, response.status_code, text, started, f"Receiver returned {response.status_code}"
    )


def _result(
    ok: bool, status: int | None, body: str, started: float, error: str
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "body": body,
        "error": error,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


async def purge_old_deliveries(ctx: dict[str, Any], days: int = 30) -> dict[str, Any]:
    """Delivery history is operational data, not memory: trim it on a schedule."""
    from datetime import timedelta

    from sqlalchemy import delete

    from common.time import utcnow
    from database.models import WebhookDelivery

    cutoff = utcnow() - timedelta(days=days)
    async with worker_session() as session:
        result = await session.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.created_at < cutoff,
                WebhookDelivery.status.in_([DeliveryStatus.SUCCEEDED, DeliveryStatus.DISABLED]),
            )
        )
        removed = int(result.rowcount or 0)
    if removed:
        logger.info("webhook.purged", removed=removed, days=days)
    return {"removed": removed, "max_attempts": MAX_ATTEMPTS}
