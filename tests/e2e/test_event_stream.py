"""The dashboard's live event stream, over a real socket.

Server-sent events cannot be tested through the in-process test transport: it collects the
whole body before returning, and this response deliberately never ends. So this suite runs
a real uvicorn server and reads the stream with a real HTTP client, which is also the only
way to prove the frames arrive *incrementally* rather than in one lump at the end.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required

from app.api.stream import HEARTBEAT_SECONDS, MAX_STREAM_SECONDS, POLL_SECONDS
from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]

# Long enough for a poll to come round, short enough that a broken stream fails the test
# rather than hanging it.
READ_TIMEOUT = 10.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def live_server():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)

    with TestClient(create_app()) as client:
        unique = str(int(time.time() * 1000))
        signup = client.post(
            "/v1/auth/signup",
            json={
                "email": f"stream-{unique}@example.com",
                "password": "a-strong-password",
                "organization_name": f"Stream {unique}",
            },
        )
        assert signup.status_code == 201, signup.text
        token = signup.json()["tokens"]["access_token"]
        project = client.post(
            "/v1/projects", json={"name": "Stream"}, headers={"Authorization": f"Bearer {token}"}
        ).json()

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the API server did not start"

    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "project_id": project["id"],
            "api_key": project["api_key"],
            "token": token,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        with engine.begin() as connection:
            Base.metadata.drop_all(connection)
        engine.dispose()


def track(server: dict, message: str) -> str:
    """Ingest one event over HTTP and return its id."""
    response = httpx.post(
        f"{server['base_url']}/v1/events",
        json={
            "customer_id": "cus_stream",
            "event_type": "support_message",
            "data": {"message": message},
        },
        headers={"X-API-Key": server["api_key"]},
        timeout=READ_TIMEOUT,
    )
    assert response.status_code in (200, 202), response.text
    return response.json()["event_id"]


def frames(lines: Iterator[str], *, want: int, kinds: tuple[str, ...] = ("event",)) -> list[dict]:
    """Read SSE frames until ``want`` of the wanted kinds have arrived."""
    collected: list[dict] = []
    current: str | None = None
    for line in lines:
        if line.startswith("event: "):
            current = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            payload["_kind"] = current
            collected.append(payload)
            if sum(1 for item in collected if item["_kind"] in kinds) >= want:
                break
    return collected


def test_the_stream_replays_history_then_follows_new_events(live_server):
    """The two things a live view has to do, in one connection."""
    first = track(live_server, "The importer stopped overnight.")

    with (
        httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, read=READ_TIMEOUT)) as client,
        client.stream(
            "GET",
            f"{live_server['base_url']}/v1/projects/{live_server['project_id']}/events/stream",
            params={"since_seconds": 300},
            headers={"Authorization": f"Bearer {live_server['token']}"},
        ) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"].startswith("no-cache")
        assert response.headers["x-accel-buffering"] == "no"

        lines = response.iter_lines()

        # 1. The replay: the event that existed before the stream opened.
        replayed = frames(lines, want=1)
        assert replayed[0]["_kind"] == "open"
        event = next(item for item in replayed if item["_kind"] == "event")
        assert event["id"] == first
        assert event["change"] in ("created", "processed")

        # 2. Following: something that happens *while* the stream is open arrives without
        #    reconnecting — which is the whole point of the endpoint.
        second = track(live_server, "And now the exporter is failing too.")
        following = frames(lines, want=1)
        assert any(item.get("id") == second for item in following), following


def test_the_stream_is_incremental_not_buffered(live_server):
    """A frame must be readable before the response ends; the response never ends."""
    track(live_server, "Something to replay.")

    started = time.monotonic()
    with (
        httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, read=READ_TIMEOUT)) as client,
        client.stream(
            "GET",
            f"{live_server['base_url']}/v1/projects/{live_server['project_id']}/events/stream",
            params={"since_seconds": 300},
            headers={"Authorization": f"Bearer {live_server['token']}"},
        ) as response,
    ):
        first_frame = frames(response.iter_lines(), want=1)
    elapsed = time.monotonic() - started

    assert first_frame
    # Well under the stream's own ceiling: the data arrived because it was flushed, not
    # because the response finished.
    assert elapsed < MAX_STREAM_SECONDS / 10


def test_a_stream_without_a_token_is_refused(live_server):
    response = httpx.get(
        f"{live_server['base_url']}/v1/projects/{live_server['project_id']}/events/stream",
        timeout=READ_TIMEOUT,
    )
    assert response.status_code in (401, 403)


def test_another_organizations_project_cannot_be_streamed(live_server):
    """Tenancy holds on the streaming path as it does everywhere else."""
    unique = str(int(time.time() * 1000))
    signup = httpx.post(
        f"{live_server['base_url']}/v1/auth/signup",
        json={
            "email": f"intruder-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"Intruder {unique}",
        },
        timeout=READ_TIMEOUT,
    )
    assert signup.status_code == 201, signup.text
    intruder = signup.json()["tokens"]["access_token"]

    response = httpx.get(
        f"{live_server['base_url']}/v1/projects/{live_server['project_id']}/events/stream",
        headers={"Authorization": f"Bearer {intruder}"},
        timeout=READ_TIMEOUT,
    )
    assert response.status_code in (403, 404)


def test_the_stream_settings_are_sane():
    """Guard rails on the constants: a stream that polls too fast is a database problem."""
    assert POLL_SECONDS >= 0.5
    assert HEARTBEAT_SECONDS < MAX_STREAM_SECONDS
    assert MAX_STREAM_SECONDS <= 3600
