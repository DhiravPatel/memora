"""Per-memory access control, over HTTP.

A restricted memory leaking through a *list* endpoint is obvious and easy to catch. The
dangerous leak is the invisible one: an answer composed for a caller without clearance
that quotes a memory they are not allowed to read, or a context block handed to an agent
with the sentence in it. Those paths get the most attention here.
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

SECRET = "The customer is disputing their salary calculation with HR."
ORDINARY = "The customer's Shopify sync fails during checkout."


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
    """An org with a restriction policy, and three keys with different clearance."""
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": f"policy-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"Policy {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    token = signup.json()["tokens"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    project = client.post("/v1/projects", json={"name": "Policy"}, headers=auth).json()
    project_id = project["id"]

    applied = client.put(
        f"/v1/projects/{project_id}/settings",
        json={
            "settings": {
                "restriction_policies": [
                    {"kind": "term", "value": ["salary", "hr"], "label": "employment matters"}
                ]
            }
        },
        headers=auth,
    )
    assert applied.status_code == 200, applied.text

    open_key = client.post(
        f"/v1/projects/{project_id}/api-keys",
        json={
            "name": "support",
            "scopes": ["memory:read", "memory:write", "customers:read", "customers:write"],
        },
        headers=auth,
    )
    assert open_key.status_code == 201, open_key.text

    cleared_key = client.post(
        f"/v1/projects/{project_id}/api-keys",
        json={
            "name": "compliance",
            "scopes": [
                "memory:read",
                "memory:write",
                "memory:restricted",
                "customers:read",
                "customers:write",
            ],
        },
        headers=auth,
    )
    assert cleared_key.status_code == 201, cleared_key.text

    return {
        "auth": auth,
        "project_id": project_id,
        "project_key": project["api_key"],          # legacy full-scope key
        "uncleared": open_key.json()["api_key"],
        "cleared": cleared_key.json()["api_key"],
    }


def key_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


@pytest.fixture(scope="module")
def customer(client: TestClient, account: dict) -> str:
    """One restricted memory and one ordinary one, written through the API."""
    external_id = "cus_policy"
    created_customer = client.post(
        "/v1/customers",
        json={"external_id": external_id, "name": "Policy Customer"},
        headers=key_headers(account["cleared"]),
    )
    assert created_customer.status_code == 201, created_customer.text
    for content, memory_type in ((SECRET, "problem"), (ORDINARY, "problem")):
        created = client.post(
            "/v1/memories",
            json={"customer_id": external_id, "content": content, "type": memory_type},
            headers=key_headers(account["cleared"]),
        )
        assert created.status_code == 201, created.text
    return external_id


# --------------------------------------------------------------- classification


def test_a_matching_memory_is_marked_restricted_when_written(client, account, customer):
    listed = client.get(
        f"/v1/customers/{customer}/memories",
        params={"limit": 50},
        headers=key_headers(account["cleared"]),
    ).json()
    by_content = {item["content"]: item for item in listed["data"]}

    assert by_content[SECRET]["sensitivity"] == "restricted"
    assert by_content[ORDINARY]["sensitivity"] == "normal"
    assert by_content[SECRET]["metadata"]["restricted_by"] == "employment matters"


# ------------------------------------------------------------------- list reads


def test_an_uncleared_key_does_not_receive_the_memory(client, account, customer):
    listed = client.get(
        f"/v1/customers/{customer}/memories",
        params={"limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    contents = [item["content"] for item in listed["data"]]

    assert ORDINARY in contents
    assert SECRET not in contents
    # The total reflects what this reader can see, rather than claiming rows it withheld.
    assert listed["total"] == len(listed["data"])


def test_a_legacy_project_key_is_not_cleared(client, account, customer):
    """A key issued before restricted memory existed cannot be assumed to be trusted."""
    listed = client.get(
        f"/v1/customers/{customer}/memories",
        params={"limit": 50},
        headers=key_headers(account["project_key"]),
    ).json()
    assert SECRET not in [item["content"] for item in listed["data"]]


def test_fetching_the_memory_by_id_is_refused(client, account, customer):
    cleared = client.get(
        f"/v1/customers/{customer}/memories",
        params={"limit": 50},
        headers=key_headers(account["cleared"]),
    ).json()
    memory_id = next(item["id"] for item in cleared["data"] if item["content"] == SECRET)

    assert (
        client.get(f"/v1/memories/{memory_id}", headers=key_headers(account["cleared"])).status_code
        == 200
    )
    denied = client.get(f"/v1/memories/{memory_id}", headers=key_headers(account["uncleared"]))
    assert denied.status_code == 404, denied.text


def test_the_project_wide_list_hides_it_and_says_so(client, account, customer):
    """The list that is not scoped to a customer is the easiest one to forget to gate."""
    hidden = client.get(
        "/v1/memories",
        params={"customer_id": customer, "limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert SECRET not in [item["content"] for item in hidden["data"]]
    # Silence would send the reader hunting for a bug. The count is the honest answer.
    assert hidden["withheld"] == 1

    full = client.get(
        "/v1/memories",
        params={"customer_id": customer, "limit": 50},
        headers=key_headers(account["cleared"]),
    ).json()
    assert SECRET in [item["content"] for item in full["data"]]
    assert full["withheld"] == 0


# ------------------------------------------------- the paths that leak silently


def test_an_answer_never_quotes_a_memory_the_caller_cannot_read(client, account, customer):
    """The leak that would be invisible: it surfaces as prose, not as a row."""
    question = {"customer_id": customer, "query": "What is going on with this customer?"}

    cleared = client.post(
        "/v1/memory/query", json=question, headers=key_headers(account["cleared"])
    ).json()
    uncleared = client.post(
        "/v1/memory/query", json=question, headers=key_headers(account["uncleared"])
    ).json()

    assert "salary" not in uncleared["answer"].lower()
    assert "hr" not in uncleared["answer"].lower().split()
    assert all("salary" not in memory["content"].lower() for memory in uncleared["memories"])
    # The cleared caller still gets a full answer, so the gate is not simply breaking search.
    assert cleared["answer"]


def test_context_for_an_agent_respects_clearance(client, account, customer):
    body = {"customer_id": customer, "task": "support_response", "format": "text"}

    uncleared = client.post(
        "/v1/memory/context", json=body, headers=key_headers(account["uncleared"])
    ).json()
    text = (uncleared.get("prompt_text") or "") + str(uncleared["customer_context"])
    assert "salary" not in text.lower()

    cleared = client.post(
        "/v1/memory/context", json=body, headers=key_headers(account["cleared"])
    ).json()
    cleared_text = (cleared.get("prompt_text") or "") + str(cleared["customer_context"])
    assert "salary" in cleared_text.lower()


def test_search_does_not_return_it_even_when_asked_for_directly(client, account, customer):
    """Searching for the restricted words must not confirm the memory exists."""
    found = client.post(
        "/v1/memory/search",
        json={"customer_id": customer, "query": "salary dispute with HR", "limit": 10},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert all("salary" not in memory["content"].lower() for memory in found["memories"])


def test_an_export_respects_clearance(client, account, customer):
    bundle = client.get(
        f"/v1/customers/{customer}/export", headers=key_headers(account["uncleared"])
    ).json()
    assert all("salary" not in memory["content"].lower() for memory in bundle["memories"])

    full = client.get(
        f"/v1/customers/{customer}/export", headers=key_headers(account["cleared"])
    ).json()
    assert any("salary" in memory["content"].lower() for memory in full["memories"])


# ------------------------------------------------------------------------ writes


def test_an_uncleared_caller_cannot_correct_or_delete_it(client, account, customer):
    cleared = client.get(
        f"/v1/customers/{customer}/memories",
        params={"limit": 50},
        headers=key_headers(account["cleared"]),
    ).json()
    memory_id = next(item["id"] for item in cleared["data"] if item["content"] == SECRET)

    feedback = client.post(
        f"/v1/memories/{memory_id}/feedback",
        json={"verdict": "reject"},
        headers=key_headers(account["uncleared"]),
    )
    assert feedback.status_code == 404, feedback.text

    deleted = client.delete(
        f"/v1/memories/{memory_id}", headers=key_headers(account["uncleared"])
    )
    assert deleted.status_code == 404, deleted.text


def test_a_correction_is_classified_on_its_own_words(client, account):
    """A correction is new text: it can introduce a restricted term, or remove one."""
    external_id = "cus_correct"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=key_headers(account["cleared"])
    )
    created = client.post(
        "/v1/memories",
        json={
            "customer_id": external_id,
            "content": "The customer asked about their invoice.",
            "type": "fact",
        },
        headers=key_headers(account["cleared"]),
    ).json()
    assert created["sensitivity"] == "normal"

    corrected = client.post(
        f"/v1/memories/{created['id']}/feedback",
        json={
            "verdict": "correct",
            "content": "The customer asked about their salary, not their invoice.",
        },
        headers=key_headers(account["cleared"]),
    )
    assert corrected.status_code == 200, corrected.text

    visible = client.get(
        f"/v1/customers/{external_id}/memories",
        params={"limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert all("salary" not in item["content"].lower() for item in visible["data"])
    assert visible["withheld"] == 1


def test_an_agent_session_summary_is_classified(client, account):
    """A summary is composed from the turns, so it can carry a restricted word out."""
    external_id = "cus_agent"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=key_headers(account["cleared"])
    )
    opened = client.post(
        "/v1/agent/sessions",
        json={"customer_id": external_id, "agent": "support-bot"},
        headers=key_headers(account["cleared"]),
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]

    for role, content in (
        ("user", "I want to talk about my salary deduction this month."),
        ("agent", "I have raised it with the team and will follow up."),
    ):
        turn = client.post(
            f"/v1/agent/sessions/{session_id}/turns",
            json={"role": role, "content": content},
            headers=key_headers(account["cleared"]),
        )
        assert turn.status_code == 201, turn.text

    closed = client.post(
        f"/v1/agent/sessions/{session_id}/close",
        json={"outcome": "escalated"},
        headers=key_headers(account["cleared"]),
    )
    assert closed.status_code == 200, closed.text

    visible = client.get(
        f"/v1/customers/{external_id}/memories",
        params={"limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert all("salary" not in item["content"].lower() for item in visible["data"])


# --------------------------------------------------------------- policy changes


def test_changing_the_policy_reclassifies_what_was_written_before_it(client, account):
    """Otherwise a new rule gates new memories and leaves the history wide open."""
    external_id = "cus_retro"
    made = client.post(
        "/v1/customers",
        json={"external_id": external_id},
        headers=key_headers(account["cleared"]),
    )
    assert made.status_code == 201, made.text
    created = client.post(
        "/v1/memories",
        json={
            "customer_id": external_id,
            "content": "The customer mentioned an ongoing tribunal about their contract.",
            "type": "fact",
        },
        headers=key_headers(account["cleared"]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["sensitivity"] == "normal"

    client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={
            "settings": {
                "restriction_policies": [
                    {"kind": "term", "value": ["salary", "hr"], "label": "employment matters"},
                    {"kind": "term", "value": ["tribunal"], "label": "legal"},
                ]
            }
        },
        headers=account["auth"],
    )
    _reclassify(account["project_id"])

    listed = client.get(
        f"/v1/customers/{external_id}/memories",
        params={"limit": 50},
        headers=key_headers(account["cleared"]),
    ).json()
    assert listed["data"][0]["sensitivity"] == "restricted"
    assert listed["data"][0]["metadata"]["restricted_by"] == "legal"

    hidden = client.get(
        f"/v1/customers/{external_id}/memories",
        params={"limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert hidden["total"] == 0


def test_relaxing_the_policy_releases_what_it_had_restricted(client, account):
    external_id = "cus_retro"
    client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={
            "settings": {
                "restriction_policies": [
                    {"kind": "term", "value": ["salary", "hr"], "label": "employment matters"}
                ]
            }
        },
        headers=account["auth"],
    )
    _reclassify(account["project_id"])

    visible = client.get(
        f"/v1/customers/{external_id}/memories",
        params={"limit": 50},
        headers=key_headers(account["uncleared"]),
    ).json()
    assert visible["total"] == 1
    assert "restricted_by" not in visible["data"][0]["metadata"]


# ----------------------------------------------------------------- dashboard


def test_the_dashboard_admin_is_cleared(client, account, customer):
    """An owner administering the project can read what they restricted."""
    listed = client.get(
        f"/v1/projects/{account['project_id']}/customers/{customer}/memories",
        params={"limit": 50},
        headers=account["auth"],
    ).json()
    assert SECRET in [item["content"] for item in listed["data"]]


def test_health_is_computed_over_everything(client, account, customer):
    """A score is an aggregate, not content: two readers must not see different numbers."""
    by_key = client.get(
        f"/v1/customers/{customer}/health", headers=key_headers(account["uncleared"])
    ).json()
    by_admin = client.get(
        f"/v1/projects/{account['project_id']}/customers/{customer}/health",
        headers=account["auth"],
    ).json()
    assert by_key["score"] == by_admin["score"]


def _reclassify(project_id: str) -> None:
    """Run the real reclassification job inline, on its own engine and loop.

    The worker's own session factory is bound to the API's event loop, so the job is given
    a session made here instead — the walk itself is the shipped one, not a copy.
    """
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from common.settings import get_settings as _get_settings
    from worker.tasks.policy import reclassify_project

    async def run() -> None:
        engine = create_async_engine(_get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await reclassify_project(session, project_id)
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)
