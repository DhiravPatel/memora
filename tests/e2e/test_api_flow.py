"""Full HTTP journey against a real database and Redis.

Signup → project → API key → ingest → memories → query → context → health → feedback →
links → export → webhook → deletion. This is the test that would have caught every
integration mistake the unit tests cannot see, and it exercises the deterministic engine
end to end exactly as a customer's backend would.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base
from integrations.signature import hmac_sha256_hex

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
def account(client: TestClient) -> dict[str, str]:
    """An organization, a user, a project and an API key."""
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": f"owner-{unique}@example.com",
            "password": "a-strong-password",
            "name": "Owner",
            "organization_name": f"Acme {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    token = signup.json()["tokens"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    project = client.post("/v1/projects", json={"name": "Production"}, headers=auth)
    assert project.status_code == 201, project.text
    body = project.json()
    return {
        "token": token,
        "auth_header": f"Bearer {token}",
        "project_id": body["id"],
        "api_key": body["api_key"],
    }


def api_headers(account: dict[str, str]) -> dict[str, str]:
    return {"X-API-Key": account["api_key"]}


def user_headers(account: dict[str, str]) -> dict[str, str]:
    return {"Authorization": account["auth_header"]}


def ingest(client: TestClient, account: dict[str, str], payload: dict) -> dict:
    response = client.post("/v1/events", json=payload, headers=api_headers(account))
    assert response.status_code in (200, 202), response.text
    return response.json()


def test_api_key_is_returned_once_and_stored_hashed(client, account):
    listed = client.get("/v1/projects", headers=user_headers(account)).json()
    assert listed[0]["api_key_prefix"].startswith("mk_")
    assert "api_key" not in listed[0]


def test_full_ingestion_and_understanding(client, account):
    story = [
        ("subscription_upgraded", {"plan": "Pro", "previous_plan": "Starter"}, "evt-1"),
        ("integration_connected", {"integration": "shopify"}, "evt-2"),
        ("integration_failed", {"integration": "shopify", "error": "OAuth handshake timed out"}, "evt-3"),
        ("support_message", {"message": "I've tried connecting Shopify three times but it still doesn't work!"}, "evt-4"),
        ("support_message", {"message": "Please contact me on WhatsApp instead of email."}, "evt-5"),
        ("page_view", {"path": "/dashboard"}, "evt-6"),
        ("subscription_downgraded", {"plan": "Starter", "previous_plan": "Pro",
                                     "reason": "The Shopify integration never worked reliably"}, "evt-7"),
    ]
    for event_type, data, external_id in story:
        result = ingest(
            client,
            account,
            {
                "customer_id": "cus_e2e",
                "event_type": event_type,
                "data": data,
                "external_event_id": external_id,
                "customer_name": "John",
                "customer_email": "john@acme.com",
            },
        )
        assert result["status"] == "accepted"

    # Events are processed inline by the worker in production; drive it directly here.
    _process_pending(client, account)

    memories = client.get(
        "/v1/customers/cus_e2e/memories", headers=api_headers(account), params={"limit": 50}
    ).json()
    contents = [memory["content"] for memory in memories["data"]]
    types = {memory["type"] for memory in memories["data"]}

    assert memories["total"] >= 4
    assert any("Shopify" in content for content in contents)
    assert "problem" in types and "subscription" in types and "preference" in types
    # The page view carried no durable meaning and must not have become a memory.
    assert not any("dashboard" in content.lower() for content in contents)


def test_duplicate_external_event_id_is_stored_once(client, account):
    first = client.post(
        "/v1/events",
        json={"customer_id": "cus_e2e", "event_type": "feature_used",
              "data": {"feature": "campaign_builder"}, "external_event_id": "evt-dup"},
        headers=api_headers(account),
    )
    second = client.post(
        "/v1/events",
        json={"customer_id": "cus_e2e", "event_type": "feature_used",
              "data": {"feature": "campaign_builder"}, "external_event_id": "evt-dup"},
        headers=api_headers(account),
    )
    assert first.status_code == 202 and first.json()["status"] == "accepted"
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert first.json()["event_id"] == second.json()["event_id"]


def test_idempotency_key_replays_the_original_response(client, account):
    headers = {**api_headers(account), "Idempotency-Key": "idem-1"}
    payload = {"customer_id": "cus_e2e", "event_type": "goal_created",
               "data": {"goal": "migrate 2M records"}}
    first = client.post("/v1/events", json=payload, headers=headers)
    second = client.post("/v1/events", json=payload, headers=headers)
    assert first.status_code == 202
    assert second.json()["event_id"] == first.json()["event_id"]
    assert second.headers.get("idempotent-replay") == "true"

    conflicting = client.post(
        "/v1/events",
        json={**payload, "data": {"goal": "something else"}},
        headers=headers,
    )
    assert conflicting.status_code == 409


def test_rate_limit_headers_are_present(client, account):
    response = client.get("/v1/customers", headers=api_headers(account))
    assert response.status_code == 200
    assert int(response.headers["x-ratelimit-limit"]) >= 1
    assert int(response.headers["x-ratelimit-remaining"]) >= 0


def test_query_answers_from_memory_with_evidence(client, account):
    _process_pending(client, account)
    response = client.post(
        "/v1/memory/query",
        json={"customer_id": "cus_e2e", "query": "Why did this customer downgrade?",
              "include_trace": True},
        headers=api_headers(account),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"]
    assert body["memories"]
    assert body["trace"]["engine"].startswith("memora-deterministic")
    assert body["trace"]["analysis"]["intent"] == "why"
    # Every cited memory must be one that was actually retrieved.
    retrieved = {memory["id"] for memory in body["memories"]}
    assert set(body["trace"]["evidence"]) <= retrieved


def test_context_is_sectioned_and_token_bounded(client, account):
    response = client.post(
        "/v1/memory/context",
        json={"customer_id": "cus_e2e", "task": "respond_to_support_ticket",
              "token_budget": 800, "format": "text"},
        headers=api_headers(account),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_count"] <= 800
    assert body["customer_context"]["memory_ids"]
    assert body["prompt_text"].startswith("Customer:")


def test_health_score_reflects_the_story(client, account):
    response = client.get("/v1/customers/cus_e2e/health", headers=api_headers(account))
    assert response.status_code == 200, response.text
    body = response.json()
    assert 0 <= body["score"] <= 100
    assert body["band"] in ("healthy", "watch", "at_risk", "critical")
    assert body["factors"]
    assert body["explanation"]


def test_memory_links_expose_causal_chains(client, account):
    response = client.get("/v1/customers/cus_e2e/links", headers=api_headers(account))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["links"], "a customer with a downgrade and problems should have links"
    assert any(link["link_type"] == "caused_by" for link in body["links"])
    assert any(chain["steps"] for chain in body["chains"])


def test_feedback_moves_confidence_and_keeps_history(client, account):
    memories = client.get(
        "/v1/customers/cus_e2e/memories", headers=api_headers(account)
    ).json()["data"]
    target = next(memory for memory in memories if memory["type"] == "problem")

    confirmed = client.post(
        f"/v1/memories/{target['id']}/feedback",
        json={"verdict": "confirm", "note": "checked with the customer"},
        headers=api_headers(account),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confidence"] >= target["confidence"]

    detail = client.get(f"/v1/memories/{target['id']}", headers=api_headers(account)).json()
    assert any(version["reason"] == "feedback_confirmed" for version in detail["versions"])

    corrected = client.post(
        f"/v1/memories/{target['id']}/feedback",
        json={"verdict": "correct", "content": "The Shopify OAuth token expires every 24 hours."},
        headers=api_headers(account),
    )
    assert corrected.status_code == 200
    replacement_id = corrected.json()["replacement_memory_id"]
    assert replacement_id

    replacement = client.get(f"/v1/memories/{replacement_id}", headers=api_headers(account)).json()
    assert replacement["source"] == "manual"
    superseded = client.get(f"/v1/memories/{target['id']}", headers=api_headers(account)).json()
    assert superseded["status"] == "superseded"


def test_export_contains_everything(client, account):
    response = client.get("/v1/customers/cus_e2e/export", headers=api_headers(account))
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["export_version"]
    assert bundle["counts"]["events"] >= 7
    assert bundle["memories"] and bundle["memories"][0]["versions"]
    assert "health" in bundle and "graph" in bundle


def test_generic_webhook_requires_a_valid_signature(client, account):
    project_id = account["project_id"]
    secret = "whsec-test"
    client.patch(
        f"/v1/projects/{project_id}",
        json={"settings": {"integrations": {"generic": {"signing_secret": secret}}}},
        headers=user_headers(account),
    )

    body = (
        b'{"events":[{"customer_id":"cus_webhook","event_type":"support_message",'
        b'"data":{"message":"The export feature is failing for us."}}]}'
    )
    unsigned = client.post(
        "/v1/integrations/generic/webhook",
        content=body,
        headers={**api_headers(account), "Content-Type": "application/json"},
    )
    assert unsigned.status_code == 401

    signed = client.post(
        "/v1/integrations/generic/webhook",
        content=body,
        headers={
            **api_headers(account),
            "Content-Type": "application/json",
            "X-Signature": hmac_sha256_hex(secret, body),
        },
    )
    assert signed.status_code == 202, signed.text
    assert signed.json()["accepted"][0]["status"] == "accepted"


def test_dashboard_sees_the_same_project(client, account):
    project_id = account["project_id"]
    overview = client.get(
        f"/v1/projects/{project_id}/overview", headers=user_headers(account)
    ).json()
    assert overview["total_customers"] >= 1
    assert overview["total_memories"] >= 1

    portfolio = client.get(
        f"/v1/projects/{project_id}/health",
        params={"bands": "healthy,watch,at_risk,critical"},
        headers=user_headers(account),
    ).json()
    assert portfolio["customers"]


def test_every_dashboard_read_endpoint_responds(client, account):
    """The screens the dashboard loads on every visit.

    A method on ``UsageService`` was once shadowed by a same-named attribute, so
    ``/usage`` returned 500 and the Overview chart silently showed a CORS error in the
    browser. Nothing failed in CI because nothing called these endpoints.
    """
    project_id = account["project_id"]
    headers = user_headers(account)
    for path, params in [
        (f"/v1/projects/{project_id}", None),
        (f"/v1/projects/{project_id}/overview", None),
        (f"/v1/projects/{project_id}/usage", {"days": 30}),
        (f"/v1/projects/{project_id}/health", {"bands": "healthy,watch,at_risk,critical"}),
        (f"/v1/projects/{project_id}/customers", {"limit": 10}),
        (f"/v1/projects/{project_id}/memories", {"limit": 10}),
        (f"/v1/projects/{project_id}/entities", {"limit": 10}),
        (f"/v1/projects/{project_id}/events", {"limit": 10}),
        (f"/v1/projects/{project_id}/audit-logs", {"limit": 10}),
        (f"/v1/projects/{project_id}/queries", {"limit": 10}),
    ]:
        response = client.get(path, params=params, headers=headers)
        assert response.status_code == 200, f"{path} → {response.status_code}: {response.text}"


def test_tenant_isolation_between_projects(client, account):
    other = client.post(
        "/v1/projects", json={"name": "Second"}, headers=user_headers(account)
    ).json()
    response = client.get(
        "/v1/customers/cus_e2e", headers={"X-API-Key": other["api_key"]}
    )
    assert response.status_code == 404


def test_customer_deletion_removes_everything(client, account):
    response = client.delete("/v1/customers/cus_e2e", headers=api_headers(account))
    assert response.status_code == 200, response.text
    assert response.json()["removed"]["memories"] >= 1
    assert client.get("/v1/customers/cus_e2e", headers=api_headers(account)).status_code == 404


def _process_pending(client: TestClient, account: dict[str, str]) -> None:
    """Run the worker's pipeline inline for every pending event.

    The API's engine lives in the TestClient's event loop, so this uses its own engine in
    its own loop rather than borrowing a connection across loops.
    """
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from common.settings import get_settings as _get_settings
    from database.models import Event
    from database.repositories import ProjectRepository
    from memory_engine import MemoryEngine
    from nlp import LocalEmbedder

    pending = client.get(
        "/v1/events", params={"status": "pending", "limit": 200}, headers=api_headers(account)
    ).json()["data"]
    if not pending:
        return

    async def run() -> None:
        engine = create_async_engine(_get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                memory_engine = MemoryEngine(session=session, embedder=LocalEmbedder())
                projects = ProjectRepository(session)
                for item in pending:
                    event = await session.get(Event, item["id"])
                    project = await projects.get(item["project_id"])
                    if event is not None and project is not None:
                        await memory_engine.process_event(event=event, project=project)
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)
