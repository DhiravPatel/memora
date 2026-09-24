"""Why did my agent do this? — the decision trace over HTTP (§26 4.4).

The customer's history goes through the real pipeline, so the superseded problem is
superseded by consolidation itself (the Shopify fix), the restricted memory is restricted
by the project's policy, and the feedback memory is outside the support profile by type.
Only the expiry of the intent is staged, since nothing expires in a test's lifetime.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import database_required, run_worker

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]

SCOPES = ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]


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
def client(engine):
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def world(client: TestClient, engine) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={"email": f"trace-{unique}@example.com", "password": "a-strong-password", "organization_name": f"T {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Trace"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    client.put(
        f"{base}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    profile = client.post(
        f"{base}/agent/profiles",
        json={"name": "support-agent", "readable_types": ["problem", "preference", "subscription", "fact", "intent"]},
        headers=auth,
    )
    assert profile.status_code == 201, profile.text

    def key(name: str, scopes: list[str], profile_id: str | None = None) -> str:
        created = client.post(
            f"{base}/api-keys", json={"name": name, "scopes": scopes, "agent_profile_id": profile_id}, headers=auth
        )
        assert created.status_code == 201, created.text
        return created.json()["api_key"]

    world = {
        "auth": auth,
        "base": base,
        "cleared": key("cleared", [*SCOPES, "memory:restricted"]),
        "uncleared": key("uncleared", SCOPES),
        "support": key("support", SCOPES, profile.json()["id"]),
    }
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    now = datetime.now(UTC)
    for days, event_type, data in (
        (6, "support_message", {"message": "The Shopify sync fails during checkout."}),
        (5, "support_message", {"message": "The CSV importer times out on large files."}),
        (4, "support_message", {"message": "SSO login loops back to the start page."}),
        (3, "cancellation_requested", {"reason": "the Shopify sync keeps failing"}),
        (2, "support_message", {"message": "The Shopify sync works now."}),
        (1, "support_message", {"message": "The Stripe payout of their salary failed."}),
        (1, "feedback_submitted", {"message": "Setting up the Shopify integration was frustrating."}),
    ):
        sent = client.post(
            "/v1/events",
            json={"customer_id": "acme", "event_type": event_type, "data": data, "occurred_at": (now - timedelta(days=days)).isoformat()},
            headers=h(world),
        )
        assert sent.status_code == 202, sent.text
        assert run_worker(sent.json()["event_id"])["status"] == "processed"
    # The intent lapsed (nothing in a test lives long enough to expire on its own).
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE memories SET status = 'expired', expires_at = now() - interval '1 day' "
                "WHERE project_id = :p AND type = 'intent'"
            ),
            {"p": project["id"]},
        )
    return world


def h(world: dict, key: str = "cleared") -> dict:
    return {"X-API-Key": world[key]}


def ask(client, world, question: str, key: str = "cleared", limit: int = 2) -> str:
    response = client.post(
        "/v1/memory/query", json={"customer_id": "acme", "query": question, "limit": limit}, headers=h(world, key)
    )
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def trace(client, world, run_id: str, key: str = "cleared") -> dict:
    response = client.get(f"/v1/agent/runs/{run_id}/trace", headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


def by_reason(body: dict) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for item in body["ignored"]:
        found.setdefault(item["reason"], []).append(item)
    return found


def test_the_problem_was_closed_by_the_pipeline(client, world):
    """The premise of the superseded case: consolidation closed the Shopify problem."""
    problems = client.get("/v1/customers/acme/memories", params={"type": "problem"}, headers=h(world)).json()["data"]
    assert not any("Shopify sync fails" in memory["content"] for memory in problems)


def test_what_the_agent_was_given_and_what_it_was_not(client, world):
    body = trace(client, world, ask(client, world, "Is the Shopify sync still failing?"))
    assert body["recorded"] is True
    assert body["decision"]["kind"] == "answer" and body["decision"]["confidence"] is not None
    assert body["given"] and {item["verdict"] for item in body["given"]} <= {"cited", "not_cited"}
    assert all(item["why"].startswith(f"#{item['rank']}") for item in body["given"])

    ignored = by_reason(body)
    # The fix superseded the latest report of the problem; the trace names the fix.
    superseded = next(item for item in ignored["superseded"] if "The Shopify sync works now." in item["why"])
    assert "Shopify sync" in superseded["content"] and superseded["why"].startswith("Superseded on")
    fix = next((item for item in body["given"] if item["content_then"] == "The Shopify sync works now."), None)
    if fix is not None:  # the fix was handed to the agent, and the trace says so
        assert superseded["replacement_rank"] == fix["rank"]
        assert f"which the agent was given (#{fix['rank']})" in superseded["why"]

    assert ignored["expired"][0]["why"].startswith("Expired on")
    ranked_out = ignored.get("below_cut", []) + ignored.get("type_cap", [])
    assert ranked_out, "with limit=2, retrieval passed over candidates"
    assert all(item["position"] and item["position"] > 1 for item in ranked_out)
    assert body["cut"]["limit"] == 2
    assert any(line.startswith("Considered but not given:") for line in body["narrative"])


def test_a_reader_without_clearance_is_told_something_was_withheld_not_what(client, world):
    body = trace(client, world, ask(client, world, "Did any payouts fail?", key="uncleared"), key="uncleared")
    restricted = by_reason(body)["withheld_restricted"][0]
    assert restricted["visible"] is False and restricted["content"] == "[withheld]"
    assert "no clearance" in restricted["why"]
    blob = json.dumps(body).lower()
    assert "salary" not in blob and "stripe" not in blob


def test_the_profile_is_named_when_it_hid_a_memory(client, world):
    body = trace(client, world, ask(client, world, "How do they feel about the Shopify integration?", key="support"), key="support")
    profile = by_reason(body)["withheld_profile"][0]
    assert profile["type"] == "feedback"
    assert profile["why"] == "The support-agent profile does not read feedback memories."
    assert profile["content"] == "[withheld]", "the reader is the same profile: it may not read it either"
    assert body["held_back"]["profile"] == "support-agent"


def test_a_trace_is_shaped_for_whoever_reads_it(client, world):
    run_id = ask(client, world, "Did any payouts fail?", key="cleared", limit=5)
    cleared = trace(client, world, run_id)
    assert "salary" in json.dumps(cleared).lower()
    uncleared = trace(client, world, run_id, key="uncleared")
    assert "salary" not in json.dumps(uncleared).lower()
    hidden = [item for item in uncleared["given"] if not item["visible"]]
    assert hidden and all(item["content_then"] == "[withheld]" for item in hidden)
    assert uncleared["decision"]["answer"] == "[withheld]" and uncleared["decision"]["reasoning"] == []

    dashboard = client.get(f"{world['base']}/agent/runs/{run_id}/trace", headers=world["auth"])
    assert dashboard.status_code == 200 and dashboard.json()["given"] == cleared["given"]


def test_a_context_trace_names_what_the_budget_dropped(client, world):
    response = client.post(
        "/v1/memory/context", json={"customer_id": "acme", "task": "support_response", "token_budget": 200}, headers=h(world)
    )
    assert response.status_code == 200, response.text
    body = trace(client, world, response.json()["run_id"])
    assert body["decision"]["kind"] == "context" and body["decision"]["token_budget"] == 200
    assert body["given"] and {item["verdict"] for item in body["given"]} == {"given"}
    sentences = {
        "token_budget": "Retrieved, but dropped to fit the token budget of 200 tokens.",
        "duplicate": "Said what a memory already in the context says, so it was left out.",
    }
    handed = {item["id"] for item in body["given"]}
    for item in body["ignored"]:
        assert item["id"] not in handed, "nothing is both given and ignored"
        if item["reason"] in sentences:
            assert item["why"] == sentences[item["reason"]]
        elif item["reason"] == "section_cap":
            assert item["why"].startswith("Its section of the context was full")


def test_runs_recorded_before_traces_say_so(client, world, engine):
    run_id = ask(client, world, "What plan are they on?")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE query_logs SET trace = trace - 'unseen' - 'passed_over' - 'cut' WHERE id = :id"), {"id": run_id}
        )
    body = trace(client, world, run_id)
    assert body["recorded"] is False and body["ignored"] == []
    assert client.get("/v1/agent/runs/run_nope/trace", headers=h(world)).status_code == 404
