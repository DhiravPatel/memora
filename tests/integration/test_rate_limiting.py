"""The token bucket and the queue's failure behaviour, against a real Redis.

The behaviour that matters is the one the old fixed-window counter got wrong: a caller
must not be able to spend two windows' worth of allowance in the space of a second by
straddling a window boundary. Refill is driven by an injected clock so the test can move
time without sleeping through it.
"""

from __future__ import annotations

import os

import pytest
from arq.connections import RedisSettings

from app.core.ratelimit import (
    WINDOW_SECONDS,
    RateLimitState,
    _take,
    check_rate_limit,
    limit_for,
    quota_for,
    reset_script_cache,
)
from common.errors import RateLimitError
from common.settings import get_settings
from database.models import Project


def _redis_available() -> bool:
    if os.environ.get("SKIP_REDIS_TESTS"):
        return False
    try:
        import anyio
        from redis.asyncio import Redis

        async def ping() -> bool:
            client = Redis.from_url(get_settings().redis_url)
            try:
                return bool(await client.ping())
            finally:
                await client.aclose()

        return bool(anyio.run(ping))
    except Exception:  # noqa: BLE001 - absence of Redis is the thing being detected
        return False


redis_required = pytest.mark.skipif(
    not _redis_available(), reason="Set up Redis (or unset SKIP_REDIS_TESTS) to run these."
)

pytestmark = [pytest.mark.integration, redis_required]


@pytest.fixture(autouse=True)
def fresh_queue_pool():
    """Each test gets its own Redis pool.

    ``app.core.queue`` caches one pool globally, which is right in the API process (one
    loop for the lifetime of the app) and wrong here, where every test runs in a new event
    loop and would otherwise inherit a pool bound to a closed one.
    """
    import app.core.queue as queue

    queue._pool = None
    yield
    queue._pool = None


@pytest.fixture
async def redis():
    from redis.asyncio import Redis

    client = Redis.from_url(get_settings().redis_url)
    reset_script_cache()
    yield client
    await client.aclose()


@pytest.fixture
def key(request) -> str:
    return f"test:ratelimit:{request.node.name}"


def project(limit: int | None = None, quota: int | None = None) -> Project:
    settings: dict[str, object] = {}
    if limit is not None:
        settings["rate_limit_per_minute"] = limit
    if quota is not None:
        settings["monthly_event_quota"] = quota
    return Project(id=f"prj_test_{limit}_{quota}", name="Test", settings=settings)


async def take(redis, key: str, *, capacity: float, rate: float, now: float, cost: int = 1):
    return await _take(
        redis, key, capacity=capacity, refill_rate=rate, cost=cost, now=now, ttl_ms=120_000
    )


async def test_a_full_bucket_allows_a_whole_window_at_once(redis, key):
    await redis.delete(key)
    for _ in range(10):
        allowed, _, _ = await take(redis, key, capacity=10, rate=10 / 60, now=1000.0)
        assert allowed

    refused, tokens, retry_after_ms = await take(redis, key, capacity=10, rate=10 / 60, now=1000.0)
    assert refused is False
    assert tokens < 1
    assert retry_after_ms > 0


async def test_a_burst_cannot_straddle_a_boundary(redis, key):
    """The bug the fixed window had: 2× the limit inside one second.

    Ten requests at the end of one minute, then ten more at the start of the next, used to
    pass because the counter reset on the boundary. With a bucket, the second burst has to
    wait for tokens to refill.
    """
    await redis.delete(key)
    capacity, rate = 10, 10 / 60

    for _ in range(10):
        allowed, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=1059.9)
        assert allowed

    # One second later — a new fixed window would have started here.
    allowed, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=1060.9)
    assert allowed is False, "crossing a minute boundary must not refill the whole bucket"


async def test_tokens_refill_continuously(redis, key):
    await redis.delete(key)
    capacity, rate = 60, 1.0  # one token per second

    for _ in range(60):
        await take(redis, key, capacity=capacity, rate=rate, now=5000.0)
    refused, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=5000.0)
    assert refused is False

    # Five seconds later exactly five requests get through, and the sixth does not.
    for index in range(5):
        allowed, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=5005.0)
        assert allowed, f"token {index} should have refilled"
    refused, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=5005.0)
    assert refused is False


async def test_the_bucket_never_fills_past_capacity(redis, key):
    """An idle week does not bank a week's worth of requests."""
    await redis.delete(key)
    capacity, rate = 10, 10 / 60

    await take(redis, key, capacity=capacity, rate=rate, now=1000.0)
    for _ in range(10):
        allowed, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=1000.0 + 604_800)
        assert allowed
    refused, _, _ = await take(redis, key, capacity=capacity, rate=rate, now=1000.0 + 604_800)
    assert refused is False


async def test_a_take_larger_than_one_costs_more(redis, key):
    await redis.delete(key)
    allowed, tokens, _ = await take(redis, key, capacity=10, rate=1.0, now=200.0, cost=4)
    assert allowed
    assert tokens == pytest.approx(6.0)


async def test_check_rate_limit_reports_headers_and_refuses(redis):
    """The whole path, including the settings lookup and the header contract."""
    target = project(limit=3)
    await redis.delete(f"ratelimit:{target.id}")

    state: RateLimitState | None = None
    for _ in range(3):
        state = await check_rate_limit(target)
        assert state.enforced
    assert state is not None
    headers = state.headers()
    assert headers["X-RateLimit-Limit"] == "3"
    assert int(headers["X-RateLimit-Remaining"]) >= 0
    assert int(headers["X-RateLimit-Reset"]) > 0

    with pytest.raises(RateLimitError) as refused:
        await check_rate_limit(target)
    assert refused.value.details["limit"] == 3
    assert refused.value.details["retry_after"] >= 1


async def test_limits_are_per_project(redis):
    first, second = project(limit=2), project(limit=5)
    second.id = "prj_test_second"
    await redis.delete(f"ratelimit:{first.id}", f"ratelimit:{second.id}")

    for _ in range(2):
        await check_rate_limit(first)
    with pytest.raises(RateLimitError):
        await check_rate_limit(first)

    # The other project is untouched by its neighbour's burst.
    state = await check_rate_limit(second)
    assert state.enforced


def test_settings_are_read_with_sane_fallbacks():
    assert limit_for(project(limit=250)) == 250
    assert limit_for(Project(id="p", name="n", settings={})) == 600
    assert limit_for(Project(id="p", name="n", settings={"rate_limit_per_minute": "nonsense"})) == 600
    assert limit_for(project(limit=-5)) == 1
    assert quota_for(project(quota=1000)) == 1000
    assert quota_for(Project(id="p", name="n", settings={})) == 0


async def test_a_redis_outage_fails_open(monkeypatch):
    """Losing an event is permanent; letting a few extra through is not."""
    import app.core.ratelimit as module

    async def broken_queue():
        raise ConnectionError("redis is down")

    monkeypatch.setattr("app.core.queue.get_queue", broken_queue)
    state = await module.check_rate_limit(project(limit=5))
    assert state.allowed is True
    assert state.enforced is False
    assert state.remaining == 5
    # Nothing is asserted about headers: an unenforced limit does not set them.


async def test_the_window_constant_is_a_minute():
    assert WINDOW_SECONDS == 60


# ------------------------------------------------------------- queue behaviour


async def test_a_dead_redis_fails_fast_rather_than_stalling_ingestion(monkeypatch):
    """The bug this guards: an unreachable Redis used to block every request ~30s.

    ``arq.create_pool`` retries with backoff by default, which turned an outage into a
    stall on the ingest path — technically not an error, and far worse than one.
    """
    import time as clock

    from app.core import queue as module

    module.reset_breaker()
    monkeypatch.setattr(
        module, "redis_settings", lambda *_: RedisSettings(host="127.0.0.1", port=1)
    )

    started = clock.monotonic()
    assert await module.enqueue("process_event", "evt_x") is None
    first = clock.monotonic() - started
    assert first < module.CONNECT_TIMEOUT_SECONDS + 2, f"took {first:.1f}s to give up"

    # The breaker is now open: the next call does not even try.
    started = clock.monotonic()
    assert await module.enqueue("process_event", "evt_y") is None
    assert clock.monotonic() - started < 0.2, "an open breaker must not touch the network"

    module.reset_breaker()


async def test_queue_depth_degrades_to_zero_when_redis_is_unreachable(monkeypatch):
    from app.core import queue as module

    module.reset_breaker()
    monkeypatch.setattr(
        module, "redis_settings", lambda *_: RedisSettings(host="127.0.0.1", port=1)
    )
    assert await module.queue_depth() == 0
    module.reset_breaker()


async def test_a_live_redis_still_enqueues(redis):
    """The breaker must not be so eager that it breaks the normal path."""
    from app.core import queue as module

    module.reset_breaker()
    job_id = await module.enqueue("process_event", "evt_live_check")
    assert job_id, "a reachable Redis should accept the job"
    await module.close_queue()
    module.reset_breaker()
