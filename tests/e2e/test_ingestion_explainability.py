"""Why an event did or did not become a memory — the dry run, and the record afterwards.

The claim this file exists to defend is that the preview and the real pipeline agree.
Anything less and the preview is worse than nothing: a developer who is told one thing and
sees another stops trusting both. So the important tests here send the *same* event twice —
once to /preview and once for real — and compare.
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

pytestmark = [pytest.mark.e2e, database_required]

PROBLEM = "The Shopify sync keeps failing during checkout and we are losing orders."


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
        json={
            "email": f"explain-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"Explain {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    token = signup.json()["tokens"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    project = client.post("/v1/projects", json={"name": "Explain"}, headers=auth).json()

    full = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={
            "name": "full",
            "scopes": [
                "events:write",
                "memory:read",
                "memory:write",
                "customers:read",
                "customers:write",
            ],
        },
        headers=auth,
    )
    assert full.status_code == 201, full.text

    ingest_only = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "ingest", "scopes": ["events:write", "customers:write"]},
        headers=auth,
    )
    assert ingest_only.status_code == 201, ingest_only.text

    return {
        "auth": auth,
        "project_id": project["id"],
        "key": full.json()["api_key"],
        "ingest_key": ingest_only.json()["api_key"],
    }


def headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def customer(client: TestClient, account: dict) -> str:
    created = client.post(
        "/v1/customers",
        json={"external_id": "cus_explain", "name": "Explain Co"},
        headers=headers(account["key"]),
    )
    assert created.status_code == 201, created.text
    return "cus_explain"


def preview(client, account, customer, data, event_type="support_message"):
    response = client.post(
        "/v1/events/preview",
        json={"customer_id": customer, "event_type": event_type, "data": data},
        headers=headers(account["key"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------- the dry run


def test_a_preview_explains_what_would_be_remembered(client, account, customer):
    result = preview(client, account, customer, {"message": PROBLEM})

    assert result["would_process"] is True
    assert result["stop_reason"] is None
    assert result["memories"], "an ordinary support complaint should produce something"
    first = result["memories"][0]
    assert first["action"] == "create"
    assert first["type"] == "problem"
    assert first["reason"]
    # The trace names the rule that produced the statement, so a surprising memory is
    # traceable to the thing that read it.
    assert first["extracted_by"]


def test_a_preview_writes_nothing(client, account, customer):
    """The whole point. If a dry run had side effects it would be a worse way to send."""
    before = client.get(
        "/v1/memories", params={"customer_id": customer, "limit": 100}, headers=headers(account["key"])
    ).json()
    events_before = client.get(
        "/v1/events", params={"customer_id": customer, "limit": 100}, headers=headers(account["key"])
    ).json()

    preview(client, account, customer, {"message": "A brand new complaint about billing."})

    after = client.get(
        "/v1/memories", params={"customer_id": customer, "limit": 100}, headers=headers(account["key"])
    ).json()
    events_after = client.get(
        "/v1/events", params={"customer_id": customer, "limit": 100}, headers=headers(account["key"])
    ).json()

    assert after["total"] == before["total"]
    assert events_after["total"] == events_before["total"]


def test_a_preview_says_why_nothing_would_happen_for_an_unreadable_payload(
    client, account, customer
):
    """An empty payload, or one holding only reserved keys, has nothing to extract from.

    Worth its own message because the failure is invisible from outside: the event is
    accepted with a 202 like any other and then silently does nothing.
    """
    result = preview(client, account, customer, {"metadata": {"trace": "abc"}}, "page_view")

    assert result["would_process"] is False
    assert "no readable values" in result["stop_reason"]
    # It explains what *is* read, so the fix is in the message rather than in the source.
    assert "key: value" in result["stop_reason"]
    assert result["memories"] == []


def test_a_payload_of_ordinary_fields_is_readable_even_without_a_message_field(
    client, account, customer
):
    """The engine reads every scalar, not a fixed list of prose fields.

    Pinned because the opposite is the natural assumption, and a stop reason that claimed
    otherwise would send people looking for a 'message' field they do not need.
    """
    result = preview(client, account, customer, {"path": "/dashboard", "ms": 42}, "page_view")

    assert "path" in result["text"] and "/dashboard" in result["text"]
    assert result["stop_reason"] is not None
    assert "threshold" in result["stop_reason"]


def test_a_preview_names_the_threshold_that_filtered_the_event(client, account, customer):
    """A number the caller cannot see from outside is the worst kind of silent filter.

    The reason has to carry both numbers — what this event scored and what it needed —
    because "below the threshold" alone leaves you opening the settings page to find out
    whether you were close.
    """
    result = preview(client, account, customer, {"path": "/dashboard"}, "page_view")

    assert result["would_process"] is False
    assert "threshold" in result["stop_reason"]
    assert f"{result['importance']:.2f}" in result["stop_reason"]
    assert f"{result['threshold']:.2f}" in result["stop_reason"]


def test_a_preview_reports_what_redaction_removed(client, account, customer):
    """Otherwise the memory quietly says less than the customer wrote, and nobody knows."""
    result = preview(
        client,
        account,
        customer,
        {"message": f"{PROBLEM} Reach me at someone@example.com about it."},
    )

    assert result["redacted"] is True
    assert any(finding["kind"] == "email" for finding in result["redactions"])
    assert "someone@example.com" not in result["text"]


def test_a_preview_shows_what_a_repeat_would_merge_into(client, account, customer):
    """The second-most-asked question: why did my new event not create a memory?"""
    sent = client.post(
        "/v1/events",
        json={"customer_id": customer, "event_type": "support_message", "data": {"message": PROBLEM}},
        headers=headers(account["key"]),
    )
    assert sent.status_code == 202, sent.text
    _drain(client, account)

    result = preview(client, account, customer, {"message": PROBLEM})

    assert result["memories"], result
    repeat = result["memories"][0]
    assert repeat["action"] != "create"
    assert repeat["closest_memory_id"], "the memory it would merge into has to be named"
    assert repeat["closest_content"]


# ------------------------------------------------- the preview and the real run agree


def test_the_preview_matches_what_actually_happens(client, account):
    """The claim the whole feature rests on."""
    external_id = "cus_agreement"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=headers(account["key"])
    )
    payload = {"message": "The billing export produces the wrong totals every month end."}

    predicted = client.post(
        "/v1/events/preview",
        json={"customer_id": external_id, "event_type": "support_message", "data": payload},
        headers=headers(account["key"]),
    ).json()

    sent = client.post(
        "/v1/events",
        json={"customer_id": external_id, "event_type": "support_message", "data": payload},
        headers=headers(account["key"]),
    )
    assert sent.status_code == 202, sent.text
    _drain(client, account)

    actual = client.get(f"/v1/events/{sent.json()['event_id']}", headers=headers(account["key"])).json()

    assert actual["outcome"] is not None, "a processed event must record what happened"
    outcome = actual["outcome"]
    assert outcome["would_process"] == predicted["would_process"]
    assert outcome["stop_reason"] == predicted["stop_reason"]
    assert outcome["memory_count"] == predicted["memory_count"]
    # The sentence a human reads has to match too, or the two disagree in the only place
    # most people will ever compare them.
    assert outcome["summary"] == predicted["summary"]

    # Field by field, because a field that means one thing in the preview and another in
    # the record is worse than a missing one — it disagrees without looking like it does.
    for field in ("action", "type", "reason", "rule", "closest_memory_id", "closest_content"):
        assert [plan[field] for plan in outcome["memories"]] == [
            plan[field] for plan in predicted["memories"]
        ], f"preview and outcome disagree on {field}"

    # The one field that must *not* match: a preview creates nothing, so it has no memory.
    assert all(plan["memory_id"] is None for plan in predicted["memories"])
    assert all(plan["memory_id"] for plan in outcome["memories"])


def test_a_skipped_event_records_why(client, account):
    """After the fact, not just before. This is the support ticket, answered."""
    external_id = "cus_skipped"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=headers(account["key"])
    )
    sent = client.post(
        "/v1/events",
        json={"customer_id": external_id, "event_type": "page_view", "data": {"path": "/x"}},
        headers=headers(account["key"]),
    )
    assert sent.status_code == 202, sent.text
    _drain(client, account)

    stored = client.get(f"/v1/events/{sent.json()['event_id']}", headers=headers(account["key"])).json()

    assert stored["status"] == "skipped"
    assert stored["outcome"]["would_process"] is False
    assert stored["outcome"]["stop_reason"]
    assert stored["outcome"]["summary"]


def test_the_stored_outcome_does_not_keep_a_second_copy_of_the_text(client, account):
    """The event already holds the payload; a duplicate is a retention problem."""
    external_id = "cus_notext"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=headers(account["key"])
    )
    sent = client.post(
        "/v1/events",
        json={
            "customer_id": external_id,
            "event_type": "support_message",
            "data": {"message": PROBLEM},
        },
        headers=headers(account["key"]),
    )
    _drain(client, account)
    stored = client.get(f"/v1/events/{sent.json()['event_id']}", headers=headers(account["key"])).json()

    assert stored["outcome"]["text"] is None
    # The length survives, because "was there anything to read?" is the useful part.
    assert stored["outcome"]["text_length"] > 0


# ------------------------------------------------------------------------ scopes


def test_an_ingestion_only_key_cannot_preview(client, account, customer):
    """Preview reads memory content, so events:write alone must not reach it.

    Without this, a leaked web-server key could read a customer's memories back out of
    the target_content of a crafted preview.
    """
    refused = client.post(
        "/v1/events/preview",
        json={"customer_id": customer, "event_type": "support_message", "data": {"message": PROBLEM}},
        headers=headers(account["ingest_key"]),
    )
    assert refused.status_code == 403, refused.text


def test_previewing_for_an_unknown_customer_is_a_404(client, account):
    missing = client.post(
        "/v1/events/preview",
        json={"customer_id": "cus_nope", "event_type": "support_message", "data": {"message": "hi"}},
        headers=headers(account["key"]),
    )
    assert missing.status_code == 404, missing.text


def _drain(client: TestClient, account: dict) -> None:
    """Process everything queued, inline, on this test's own loop."""
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run() -> None:
        from sqlalchemy import select

        from common.enums import EventStatus
        from database.models import Event, Project
        from memory_engine import MemoryEngine
        from nlp import LocalEmbedder

        engine = create_async_engine(get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                project = await session.get(Project, account["project_id"])
                memory_engine = MemoryEngine(session=session, embedder=LocalEmbedder())
                rows = (
                    (
                        await session.execute(
                            select(Event).where(
                                Event.project_id == account["project_id"],
                                Event.status == EventStatus.PENDING,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    await memory_engine.process_event(event=row, project=project)
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)
