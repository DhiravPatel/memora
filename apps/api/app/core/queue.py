"""Redis queue access for the API process.

Ingestion returns 202 as soon as the event is durably stored; the heavy work happens in
the worker. If Redis is unavailable the event stays ``pending`` and the worker's sweeper
picks it up, so an enqueue failure never loses data.

Failing *fast* matters as much as never raising. ``arq.create_pool`` retries a dead Redis
several times with backoff, which turned a Redis outage into a ~30 second stall on every
single ingest request — technically not an error, and far worse than one. So connections
are attempted once, under a short deadline, and a failure opens a breaker for a few
seconds: the next requests skip Redis entirely and return 202 immediately, and the sweeper
picks the events up when Redis comes back.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from common.logging import get_logger
from common.settings import Settings, get_settings

logger = get_logger(__name__)

MEMORY_QUEUE = "memory:queue"

_pool: ArqRedis | None = None
# When the last connection attempt failed, and for how long to stop trying.
_unavailable_until: float = 0.0

# One attempt, bounded, because ingestion is on the request path.
CONNECT_TIMEOUT_SECONDS = 2.0
OPERATION_TIMEOUT_SECONDS = 2.0
# Long enough that a burst of requests makes one attempt between them, short enough that
# recovery is measured in seconds.
BREAKER_SECONDS = 5.0


class QueueUnavailable(RuntimeError):
    """Redis could not be reached within the deadline."""


def redis_settings(settings: Settings | None = None) -> RedisSettings:
    settings = settings or get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def get_queue(settings: Settings | None = None) -> ArqRedis:
    """The shared pool, connecting at most once per breaker window."""
    global _pool, _unavailable_until

    if _pool is not None:
        return _pool
    if time.monotonic() < _unavailable_until:
        raise QueueUnavailable("Redis was unreachable moments ago; not retrying yet.")

    try:
        _pool = await asyncio.wait_for(
            # retry=0: one attempt. arq's own retries are what made an outage a stall.
            create_pool(redis_settings(settings), default_queue_name=MEMORY_QUEUE, retry=0),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        _unavailable_until = time.monotonic() + BREAKER_SECONDS
        raise QueueUnavailable(str(exc) or type(exc).__name__) from exc
    return _pool


async def enqueue(job: str, *args: Any, **kwargs: Any) -> str | None:
    """Enqueue a job, returning its id.

    Never raises and never blocks for long: ingestion must neither fail nor stall on Redis.
    A dropped job is not a lost event — the row is already committed and
    ``sweep_pending_events`` re-queues anything still pending.
    """
    try:
        queue = await get_queue()
        result = await asyncio.wait_for(
            queue.enqueue_job(job, *args, _queue_name=MEMORY_QUEUE, **kwargs),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )
        return result.job_id if result else None
    except QueueUnavailable as exc:
        logger.warning("queue.unavailable", job=job, error=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - deliberately swallowed, see docstring
        _mark_unavailable()
        logger.error("queue.enqueue_failed", job=job, error=str(exc))
        return None


async def queue_depth() -> int:
    try:
        queue = await get_queue()
        return int(await asyncio.wait_for(queue.zcard(MEMORY_QUEUE), OPERATION_TIMEOUT_SECONDS))
    except Exception:  # noqa: BLE001
        return 0


def _mark_unavailable() -> None:
    """Drop the pool and open the breaker after an operation fails on a live pool."""
    global _pool, _unavailable_until
    _pool = None
    _unavailable_until = time.monotonic() + BREAKER_SECONDS


def reset_breaker() -> None:
    """Forget the breaker state. For tests, and for a deliberate reconnect."""
    global _pool, _unavailable_until
    _pool = None
    _unavailable_until = 0.0


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        closer = getattr(_pool, "aclose", None) or _pool.close
        await closer()
        _pool = None
