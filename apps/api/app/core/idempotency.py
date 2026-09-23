"""Idempotency keys for unsafe requests.

Networks retry. A client that times out and re-sends a batch of events must not create
them twice, and it must get the *same* response it would have got the first time. Events
also carry ``external_event_id`` for deduplication at the domain level; this is the
transport-level equivalent that covers any endpoint.

Keys live in Redis for 24 hours. If Redis is unavailable the request proceeds without
replay protection rather than failing — domain-level deduplication still applies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from common.logging import get_logger

logger = get_logger(__name__)

KEY_PREFIX = "idempotency"
TTL_SECONDS = 24 * 3600
MAX_KEY_LENGTH = 200


@dataclass(slots=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]
    fingerprint: str


def _redis_key(project_id: str, path: str, key: str) -> str:
    return f"{KEY_PREFIX}:{project_id}:{hashlib.sha256(f'{path}:{key}'.encode()).hexdigest()}"


def fingerprint(payload: Any) -> str:
    """Hash of the request body, so the same key with a different body is rejected."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def lookup(*, project_id: str, path: str, key: str) -> StoredResponse | None:
    from app.core.queue import get_queue

    try:
        redis = await get_queue()
        raw = await redis.get(_redis_key(project_id, path, key))
    except Exception as exc:  # noqa: BLE001 - fail open
        logger.warning("idempotency.unavailable", error=str(exc))
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return StoredResponse(
            status_code=int(payload["status_code"]),
            body=payload["body"],
            fingerprint=payload.get("fingerprint", ""),
        )
    except (ValueError, KeyError):  # pragma: no cover - corrupted entry
        return None


async def remember(
    *, project_id: str, path: str, key: str, status_code: int, body: Any, request_fingerprint: str
) -> None:
    from app.core.queue import get_queue

    try:
        redis = await get_queue()
        await redis.set(
            _redis_key(project_id, path, key),
            json.dumps(
                {"status_code": status_code, "body": body, "fingerprint": request_fingerprint},
                default=str,
            ),
            ex=TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - fail open
        logger.warning("idempotency.store_failed", error=str(exc))
