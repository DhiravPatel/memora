"""The worker under the conditions production brings: jobs racing, attempts failing mid-write.

Found running the stack for memory contracts: two events of a project's first minute of the
day were processed at once, both inserted that day's usage counter, one hit the unique key,
and the failure handler reused the broken session — so the event stayed ``pending`` with no
error recorded.
"""

from __future__ import annotations

import time

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import database_required, run_worker

import database.session as db
from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base
from database.repositories import UsageRepository

pytestmark = [pytest.mark.e2e, database_required]


@pytest.fixture(scope="module")
def engine():
    sync = create_engine(get_settings().sync_database_url)
    with sync.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    yield sync
    with sync.begin() as connection:
        Base.metadata.drop_all(connection)
    sync.dispose()


@pytest.fixture(scope="module")
def world(engine) -> dict:
    with TestClient(create_app()) as client:
        unique = str(int(time.time() * 1000))
        signup = client.post(
            "/v1/auth/signup",
            json={"email": f"worker-{unique}@example.com", "password": "a-strong-password", "organization_name": f"W {unique}"},
        )
        auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
        project = client.post("/v1/projects", json={"name": "Worker"}, headers=auth).json()
        sent = client.post(
            "/v1/events",
            json={
                "customer_id": "acme",
                "event_type": "support_message",
                "data": {"message": "The Shopify sync has failed three times this week."},
            },
            headers={"X-API-Key": project["api_key"]},
        )
        assert sent.status_code == 202, sent.text
    return {"project_id": project["id"], "event_id": sent.json()["event_id"]}


def _on_fresh_loop(job) -> None:
    """Run ``job`` on its own loop and engine, the way a worker process does."""
    saved = (db._engine, db._session_factory)
    db._engine, db._session_factory = None, None

    async def run() -> None:
        try:
            await job()
        finally:
            await db.dispose_engine()

    try:
        anyio.run(run)
    finally:
        db._engine, db._session_factory = saved


def test_concurrent_counts_of_a_new_day_are_all_kept(world, engine):
    async def count_once() -> None:
        async with db.create_session_factory()() as session:
            await UsageRepository(session).increment(project_id=world["project_id"], metric="race.test")
            await session.commit()

    async def race() -> None:
        async with anyio.create_task_group() as group:
            for _ in range(8):
                group.start_soon(count_once)

    _on_fresh_loop(race)
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT count, (SELECT count(*) FROM usage_records WHERE metric = 'race.test') FROM usage_records WHERE metric = 'race.test'")
        ).all()
    assert rows == [(8, 1)], "one row for the day, and no count lost"


def test_a_failing_attempt_leaves_nothing_but_its_failure(world, engine, monkeypatch):
    from memory_engine.engine import MemoryEngine

    async def breaks_mid_write(self, *, event, project, contract=None):
        # A write, then a flush that fails — the session is unusable until rolled back.
        await self.session.execute(text("UPDATE customers SET name = 'half-written' WHERE id = :id"), {"id": event.customer_id})
        await self.session.execute(text("INSERT INTO usage_records (id, project_id, day, metric, count, created_at, updated_at) SELECT id, project_id, day, metric, count, created_at, updated_at FROM usage_records LIMIT 1"))
        raise AssertionError("unreachable: the insert above violates the primary key")

    monkeypatch.setattr(MemoryEngine, "process_event", breaks_mid_write)
    with pytest.raises(Exception, match="duplicate key"):
        run_worker(world["event_id"])
    with engine.begin() as connection:
        status, attempts, error = connection.execute(
            text("SELECT status, attempts, error FROM events WHERE id = :id"), {"id": world["event_id"]}
        ).one()
        name = connection.execute(
            text("SELECT name FROM customers WHERE project_id = :p AND external_id = 'acme'"), {"p": world["project_id"]}
        ).scalar_one()
    assert (status, attempts) == ("pending", 1), "recorded, and left for a retry"
    assert "duplicate key" in error
    assert name != "half-written", "what the attempt wrote was undone"

    monkeypatch.undo()
    assert run_worker(world["event_id"])["status"] == "processed", "the retry succeeds"
