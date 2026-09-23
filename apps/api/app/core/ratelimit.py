"""Per-project rate limiting and quotas.

Two independent controls:

* **Rate limit** — a token bucket in Redis, evaluated by a Lua script so the read, the
  refill and the take happen atomically. A bucket refills continuously rather than
  resetting on a boundary, which is what makes it safe against bursts: under the old fixed
  window a caller could spend a full minute's allowance in the last second of one window
  and again in the first second of the next, passing 2× the limit in about a second.
* **Quota** — a monthly event allowance read from the project's own usage counters, so
  billing limits are enforced by the same numbers the dashboard shows.

Both fail *open*. A Redis outage must not stop a customer's events from being stored:
losing an event is permanent, letting a few extra through is not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from common.errors import RateLimitError
from common.logging import get_logger
from database.models import Project

logger = get_logger(__name__)

DEFAULT_LIMIT_PER_MINUTE = 600
WINDOW_SECONDS = 60
KEY_PREFIX = "ratelimit"

# Burst headroom: a bucket holds one window's worth of tokens, so a caller who has been
# quiet can still spend their whole minute at once — what they cannot do is spend two
# minutes' worth inside one.
BURST_MULTIPLIER = 1.0

# KEYS[1] = bucket key
# ARGV    = capacity, refill_per_second, cost, now (float seconds), ttl
#
# Returns: {allowed, tokens_remaining, retry_after_ms}
#
# The bucket is stored as two fields so a partially-refilled bucket survives between
# calls; the TTL is refreshed on every take, so idle projects cost nothing.
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  updated_at = now
end

-- Continuous refill: the elapsed time since the last take, times the rate.
local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  retry_after_ms = math.ceil(((cost - tokens) / refill_rate) * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('PEXPIRE', key, ttl)

return {allowed, tostring(tokens), retry_after_ms}
"""

# Cached SHA of the script, so the common path is EVALSHA rather than shipping the body.
_script_sha: str | None = None


@dataclass(slots=True)
class RateLimitState:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    enforced: bool = True
    retry_after_ms: int = 0

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_at),
        }


def limit_for(project: Project) -> int:
    configured = (project.settings or {}).get("rate_limit_per_minute", DEFAULT_LIMIT_PER_MINUTE)
    try:
        value = int(configured)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_PER_MINUTE
    return max(1, value)


def quota_for(project: Project) -> int:
    """Monthly event allowance. 0 means unlimited."""
    configured = (project.settings or {}).get("monthly_event_quota", 0)
    try:
        return max(0, int(configured))
    except (TypeError, ValueError):
        return 0


async def _take(
    redis: Any, key: str, *, capacity: float, refill_rate: float, cost: int, now: float, ttl_ms: int
) -> tuple[bool, float, int]:
    """Run the bucket script, loading it if this Redis has not seen it yet."""
    global _script_sha

    args = [capacity, refill_rate, cost, now, ttl_ms]
    if _script_sha is None:
        _script_sha = await redis.script_load(TOKEN_BUCKET_LUA)

    try:
        result = await redis.evalsha(_script_sha, 1, key, *args)
    except Exception as exc:  # noqa: BLE001
        # NOSCRIPT after a Redis restart or a failover: reload once and retry.
        if "NOSCRIPT" not in str(exc).upper():
            raise
        _script_sha = await redis.script_load(TOKEN_BUCKET_LUA)
        result = await redis.evalsha(_script_sha, 1, key, *args)

    allowed, tokens, retry_after_ms = result
    return bool(int(allowed)), float(tokens), int(retry_after_ms)


async def check_rate_limit(
    project: Project, *, cost: int = 1, window_seconds: int = WINDOW_SECONDS
) -> RateLimitState:
    """Take ``cost`` tokens from this project's bucket, or refuse."""
    from app.core.queue import get_queue  # local import: avoids a startup cycle

    limit = limit_for(project)
    capacity = max(1.0, limit * BURST_MULTIPLIER)
    refill_rate = limit / window_seconds
    now = time.time()
    key = f"{KEY_PREFIX}:{project.id}"
    # Long enough that a full bucket would have refilled anyway, so expiry can never
    # hand somebody a fresh allowance early.
    ttl_ms = int((capacity / refill_rate) * 2000)

    try:
        redis = await get_queue()
        allowed, tokens, retry_after_ms = await _take(
            redis,
            key,
            capacity=capacity,
            refill_rate=refill_rate,
            cost=cost,
            now=now,
            ttl_ms=ttl_ms,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate fail-open, see module docstring
        logger.warning("ratelimit.unavailable", project_id=project.id, error=str(exc))
        return RateLimitState(
            allowed=True,
            limit=limit,
            remaining=limit,
            reset_at=int(now) + window_seconds,
            enforced=False,
        )

    # When the bucket is full again, expressed as a timestamp for the standard header.
    seconds_to_full = (capacity - tokens) / refill_rate
    reset_at = int(now + seconds_to_full)

    if not allowed:
        retry_after = max(1, round(retry_after_ms / 1000))
        raise RateLimitError(
            f"Rate limit of {limit} requests per {window_seconds}s exceeded for this project.",
            details={
                "limit": limit,
                "reset_at": reset_at,
                "retry_after": retry_after,
                "retry_after_ms": retry_after_ms,
            },
        )

    return RateLimitState(
        allowed=True,
        limit=limit,
        remaining=int(tokens),
        reset_at=reset_at,
        retry_after_ms=retry_after_ms,
    )


async def check_quota(project: Project, *, used_this_month: int) -> None:
    quota = quota_for(project)
    if quota and used_this_month >= quota:
        raise RateLimitError(
            f"Monthly event quota of {quota} reached for this project.",
            details={"quota": quota, "used": used_this_month},
        )


def reset_script_cache() -> None:
    """Forget the cached script SHA. Used by tests and after a Redis failover."""
    global _script_sha
    _script_sha = None
