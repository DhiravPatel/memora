"""Test configuration.

The whole system is deterministic, so the suite needs no network, no API keys and no
fixtures for model behaviour. Integration and e2e tests need PostgreSQL with pgvector and
are skipped when ``TEST_DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os

# Must be set before any module reads Settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "256")
os.environ.setdefault("JWT_SECRET", "test-secret-value-for-unit-tests-only-32ch")
os.environ.setdefault("API_KEY_SECRET", "test-api-key-secret-for-unit-tests-32chars")
# Exercised with encryption on, because that is how production runs.
os.environ.setdefault("SECRETS_ENCRYPTION_KEY", "test-secrets-encryption-key-32-chars-min")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest  # noqa: E402

from common.settings import get_settings  # noqa: E402
from nlp import LocalEmbedder  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture
def embedder() -> LocalEmbedder:
    return LocalEmbedder(dimensions=256)


def requires_database() -> bool:
    return bool(os.environ.get("TEST_DATABASE_URL"))


database_required = pytest.mark.skipif(
    not requires_database(),
    reason="Set TEST_DATABASE_URL to run database-backed tests.",
)


def run_worker(event_id: str) -> dict:
    """The worker's ``process_event`` for one event, inline, on a fresh loop with its own
    engine — the real pipeline for e2e tests that go through ``POST /v1/events``."""
    import anyio

    import database.session as db
    from worker.tasks.process_event import process_event

    saved = (db._engine, db._session_factory)
    db._engine, db._session_factory = None, None
    result: dict = {}

    async def run() -> None:
        try:
            result.update(await process_event({}, event_id))
        finally:
            await db.dispose_engine()

    try:
        anyio.run(run)
    finally:
        db._engine, db._session_factory = saved
    return result
