"""The quality report diagnoses what it measures, from the evidence the system already has."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import database_required

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    with TestClient(create_app()) as test_client:
        yield test_client
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
    engine.dispose()


@pytest.fixture(scope="module")
def account(client: TestClient) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={"email": f"q-{unique}@example.com", "password": "a-strong-password", "organization_name": f"Q {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Quality"}, headers=auth).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "k", "scopes": ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]},
        headers=auth,
    ).json()["api_key"]
    return {"auth": auth, "project_id": project["id"], "key": key}


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def traffic(client: TestClient, account: dict) -> None:
    """Page views (below the threshold), a few real messages, an unreadable event, and questions."""
    for index in range(8):
        client.post(
            "/v1/events",
            json={"customer_id": "cus_q", "event_type": "page_view", "data": {"path": f"/page/{index}"}},
            headers=h(account["key"]),
        )
    for message in (
        "The Shopify sync fails during checkout.",
        "Invoices were charged twice this month.",
        "The CSV export times out on large files.",
    ):
        client.post(
            "/v1/events",
            json={"customer_id": "cus_q", "event_type": "support_message", "data": {"message": message}},
            headers=h(account["key"]),
        )
    client.post(
        "/v1/events",
        json={"customer_id": "cus_q", "event_type": "heartbeat", "data": {"metadata": {"ok": True}}},
        headers=h(account["key"]),
    )
    _drain(account["project_id"])

    for question in (
        "What is wrong with the loader?",
        "Any gizmotron outages?",
        "Is Shopify broken?",
        "Is the importer crashing?",
    ) * 4:
        client.post("/v1/memory/query", json={"customer_id": "cus_q", "query": question}, headers=h(account["key"]))


def report(client, account) -> dict:
    response = client.get("/v1/quality", headers=h(account["key"]))
    assert response.status_code == 200, response.text
    return response.json()


def test_the_report_counts_what_happened(client, account, traffic):
    body = report(client, account)
    events = body["metrics"]["events"]
    assert events["total"] == 12
    assert events["processed"] >= 3
    by_type = {entry["event_type"]: entry for entry in events["by_type"]}
    assert by_type["page_view"]["below_threshold"] == 8
    assert by_type["heartbeat"]["no_text"] == 1


def test_threshold_filtering_names_the_event_type_and_a_fix(client, account, traffic):
    diagnostics = {item["key"]: item for item in report(client, account)["diagnostics"] if item["key"] == "threshold_filtering"}
    finding = diagnostics["threshold_filtering"]
    assert "`page_view`" in finding["title"]
    assert finding["fix"]["setting"] == "event_importance.page_view"
    assert finding["fix"]["value"] > report(client, account)["metrics"]["settings"]["min_event_importance"]


def test_an_unreadable_payload_is_diagnosed(client, account, traffic):
    keys = [item["key"] for item in report(client, account)["diagnostics"]]
    assert "unreadable_payloads" in keys


def test_vocabulary_gaps_name_the_words_nobody_wrote_down(client, account, traffic):
    finding = next(item for item in report(client, account)["diagnostics"] if item["key"] == "vocabulary_gaps")
    assert "gizmotron" in finding["fix"]["terms"]
    assert "shopify" not in finding["fix"]["terms"], "a word memories do contain is not a gap"


def test_a_word_the_concept_layer_understands_is_not_a_gap(client, account, traffic):
    """"crashing" reaches the memory that says "fails" through the `broken` concept, and
    "importer" reaches the Shopify sync through `integration` — neither is a gap."""
    terms = report(client, account)["metrics"]["searches"]["unknown_terms"]
    assert "crashing" not in terms and "importer" not in terms
    assert "gizmotron" in terms


def test_question_words_are_not_vocabulary(client, account, traffic):
    terms = report(client, account)["metrics"]["searches"]["unknown_terms"]
    assert "wrong" not in terms and "about" not in terms


def test_yield_ignores_events_processed_before_outcomes_existed(client, account, traffic):
    """An event with no recorded outcome says nothing about yield, so it must not lower it."""
    before = report(client, account)["metrics"]["events"]["yield"]
    engine = create_engine(get_settings().sync_database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO events (id, project_id, customer_id, event_type, data, source, importance, "
                "occurred_at, created_at, status, attempts) "
                "SELECT 'evt_legacy', project_id, customer_id, 'support_message', '{}'::jsonb, 'api', 0.8, "
                "now(), now(), 'processed', 1 FROM events WHERE project_id = :project LIMIT 1"
            ),
            {"project": account["project_id"]},
        )
    engine.dispose()
    after = report(client, account)["metrics"]["events"]
    assert after["processed"] >= 1
    assert after["yield"] == before


def test_no_evaluation_is_an_info_not_a_failure(client, account, traffic):
    body = report(client, account)
    finding = next(item for item in body["diagnostics"] if item["key"] == "no_evaluation")
    assert finding["severity"] == "info"
    retrieval = next(component for component in body["components"] if component["key"] == "retrieval")
    assert retrieval["score"] is None, "unscored, not zero — nothing has been measured yet"


def test_the_score_is_the_mean_of_what_could_be_scored(client, account, traffic):
    body = report(client, account)
    scored = [component["score"] for component in body["components"] if component["score"] is not None]
    assert body["score"] == round(sum(scored) / len(scored))


def test_diagnostics_are_ordered_by_severity(client, account, traffic):
    order = {"critical": 0, "warning": 1, "info": 2}
    severities = [order[item["severity"]] for item in report(client, account)["diagnostics"]]
    assert severities == sorted(severities)


def _drain(project_id: str) -> None:
    import anyio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from common.enums import EventStatus
    from database.models import Event, Project
    from memory_engine import MemoryEngine
    from nlp import LocalEmbedder

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                project = await session.get(Project, project_id)
                memory_engine = MemoryEngine(session=session, embedder=LocalEmbedder())
                rows = (
                    await session.execute(
                        select(Event).where(Event.project_id == project_id, Event.status == EventStatus.PENDING)
                    )
                ).scalars().all()
                for row in rows:
                    await memory_engine.process_event(event=row, project=project)
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)
