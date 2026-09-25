"""The customer brief over HTTP, through the real pipeline (§26 5.2).

Three weeks ago the customer was a Starter customer with a failing export who asked not to
be called; a conversation ended five days ago; since then they upgraded, threatened to
cancel, reported a slow sync and a restricted salary problem. Every event goes through the
worker's process_event, so what the brief reads is what extraction, consolidation, the
restriction policy and the lifecycle really made of them. Only time is staged.
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
OLD = 20  # days


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
        json={"email": f"brief-{unique}@example.com", "password": "a-strong-password", "organization_name": f"B {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Briefs"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    applied = client.put(
        f"{base}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    assert applied.status_code == 200, applied.text
    world = {"auth": auth, "base": base, "project_id": project["id"]}
    for name, scopes in (("cleared", [*SCOPES, "memory:restricted"]), ("uncleared", SCOPES)):
        world[name] = client.post(f"{base}/api-keys", json={"name": name, "scopes": scopes}, headers=auth).json()["api_key"]

    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    then = datetime.now(UTC) - timedelta(days=OLD)
    send(client, world, "subscription_changed", {"plan": "starter"}, at=then)
    send(client, world, "support_message", {"message": "The payroll export fails every night."}, at=then + timedelta(minutes=1))
    send(client, world, "preferences_updated", {"message": "Please don't call us, we prefer email."}, at=then + timedelta(minutes=2))

    # A conversation that ended five days ago: "since the last conversation" starts there.
    opened = client.post("/v1/agent/sessions", json={"customer_id": "acme", "agent": "support-bot"}, headers=h(world))
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    turn = client.post(
        f"/v1/agent/sessions/{session_id}/turns",
        json={"role": "user", "content": "Is there any news on the payroll export?", "remember": False, "retrieve": False},
        headers=h(world),
    )
    assert turn.status_code == 201, turn.text
    closed = client.post(f"/v1/agent/sessions/{session_id}/close", json={"outcome": "followed up"}, headers=h(world))
    assert closed.status_code == 200, closed.text

    with engine.begin() as connection:
        for table, column in (("customer_snapshots", "taken_at"), ("customer_states", "entered_at"), ("customer_states", "exited_at")):
            connection.execute(
                text(f"UPDATE {table} SET {column} = {column} - make_interval(days => {OLD}) WHERE project_id = :p AND {column} IS NOT NULL"),
                {"p": world["project_id"]},
            )
        connection.execute(
            text(
                f"UPDATE memory_versions SET created_at = created_at - make_interval(days => {OLD}) "
                "WHERE memory_id IN (SELECT id FROM memories WHERE project_id = :p)"
            ),
            {"p": world["project_id"]},
        )
        connection.execute(
            text(
                "UPDATE agent_sessions SET closed_at = now() - interval '5 days', last_active_at = now() - interval '5 days', "
                "started_at = now() - interval '5 days' WHERE id = :s"
            ),
            {"s": session_id},
        )
    world["session_id"] = session_id

    recent = datetime.now(UTC) - timedelta(days=2)
    send(client, world, "subscription_upgraded", {"previous_plan": "starter", "plan": "pro"}, at=recent)
    send(client, world, "support_message", {"message": "We will cancel if the payroll export keeps failing."}, at=recent + timedelta(hours=1))
    send(client, world, "support_message", {"message": "The payroll export failed again last night."}, at=recent + timedelta(hours=2))
    send(client, world, "support_message", {"message": "The Shopify sync is slow and keeps timing out."}, at=recent + timedelta(hours=3))
    send(client, world, "support_message", {"message": "The Stripe payout of their salary failed."}, at=recent + timedelta(hours=4))
    return world


def h(world: dict, key: str = "cleared") -> dict:
    return {"X-API-Key": world[key]}


def send(client, world, event_type: str, data: dict, *, at: datetime, customer: str = "acme") -> None:
    sent = client.post(
        "/v1/events",
        json={"customer_id": customer, "event_type": event_type, "data": data, "occurred_at": at.isoformat()},
        headers=h(world),
    )
    assert sent.status_code == 202, sent.text
    outcome = run_worker(sent.json()["event_id"])
    assert outcome["status"] == "processed", outcome


def brief(client, world, key: str = "cleared", customer: str = "acme", **params) -> dict:
    response = client.get(f"/v1/customers/{customer}/brief", params=params, headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------------- tests


def test_the_brief_leads_with_what_matters(client, world):
    body = brief(client, world)
    assert body["headline"].startswith("Acme: ")
    assert "On the Pro plan (upgraded from Starter 2 days ago), customer for 2 weeks." in body["headline"]
    assert body["headline"].endswith("3 open problems; said they may cancel.")

    points = body["talking_points"]
    assert points[0] == (
        "They said they may leave: “The customer will cancel if the payroll export keeps failing”. "
        "Acknowledge it before anything else."
    )
    # The threat was split from the problem it hangs on, and every payroll report met the
    # same memory: one problem, reported three times, quoted without its recurrence note.
    assert points[1] == "Still open after 2 weeks, reported 3 times: “The payroll export failed again last night”."
    assert any(point.startswith("Since the last conversation (") for point in points)
    assert points[-1] == "They prefer email."

    situation = body["situation"]
    assert situation["open_problems"] == 3
    assert situation["plan"]["name"] == "pro" and situation["plan"]["direction"] == "upgraded"
    assert "Pro plan" in situation["plan"]["statement"]
    tracks = {track["track"]: track for track in situation["lifecycle"]}
    assert tracks["lifecycle"]["state"] == "at_risk" and tracks["lifecycle"]["reasons"]

    assert len(body["open_issues"]) == 3
    payroll = body["open_issues"][0]
    assert payroll["times_reported"] == 3 and payroll["age_days"] >= 19
    assert [intent["type"] for intent in body["intents"]] == ["intent"]
    assert body["intents"][0]["kinds"] == ["cancellation"]
    assert body["preferences"]["channel"] == "email"
    assert body["preferences"]["opt_outs"] == [{"kind": "phone", "words": "asked not to be called"}]

    conversation = body["last_conversation"]
    assert conversation["id"] == world["session_id"] and conversation["agent"] == "support-bot"
    assert "payroll export? Outcome: followed up." in conversation["summary"]
    changes = body["recent_changes"]
    assert changes["window"]["basis"] == "last_session" and changes["window"]["value"] == world["session_id"]
    assert "Upgraded from Starter to Pro" in [item["title"] for item in changes["items"]]
    assert len(changes["items"]) <= 6 < changes["total"]


def test_what_not_to_do_and_a_next_step_that_respects_it(client, world):
    body = brief(client, world)
    texts = [caution["text"] for caution in body["cautions"]]
    assert texts == [
        "Don't offer an upgrade: the customer has 3 open problems; resolve them before selling.",
        "Don't send them marketing or ask for a review or reference: the customer has said they may leave; do not promote to them.",
        "Don't call them: the customer asked not to be called.",
        "Confirm the problem is fixed before you close their ticket: 3 problems still open.",
    ]
    # Standing policy — every discount needs a person — says nothing about Acme.
    assert not any(action in ("offer_discount", "issue_credit") for caution in body["cautions"] for action in caution["actions"])
    assert body["cautions"][2]["evidence"], "a caution cites the memory behind it"

    # The recommender's most urgent idea is a call; the customer asked not to be called.
    assert body["set_aside"] == [
        {"key": "retention_outreach", "action": "Get on a call about the renewal", "because": "The customer asked not to be called."}
    ]
    assert body["next_step"]["key"] in ("escalate_repeat_problem", "resolve_open_problem")
    assert "Reported 3 times since" not in body["next_step"]["action"]

    cited = set(body["evidence"])
    assert {issue["id"] for issue in body["open_issues"]} <= cited
    assert {ident for caution in body["cautions"] for ident in caution["evidence"]} <= cited
    assert set(body["next_step"]["memory_ids"]) <= cited


def test_the_brief_as_a_page(client, world):
    body = brief(client, world)
    page = client.get("/v1/customers/acme/brief", params={"format": "markdown"}, headers=h(world))
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/markdown")
    assert page.text == body["markdown"]
    assert page.text.startswith("# Acme\n\n" + body["headline"])
    for section in ("## Situation", "## Talk about", "## Don't", "## Open issues", "## What they prefer", "## What changed", "## Last conversation", "## Next step"):
        assert section in page.text, section
    assert "_Set aside: Get on a call about the renewal — the customer asked not to be called._" in page.text


def test_a_reader_without_clearance_gets_the_same_judgement_without_the_words(client, world):
    cleared, uncleared = brief(client, world), brief(client, world, key="uncleared")
    assert "salary" not in json.dumps(uncleared).lower()
    assert "salary" in json.dumps(cleared).lower()
    # Counts are not quotes: the headline and the cautions still count every open problem.
    assert uncleared["headline"] == cleared["headline"]
    assert uncleared["situation"]["open_problems"] == 3
    assert [caution["text"] for caution in uncleared["cautions"]] == [caution["text"] for caution in cleared["cautions"]]
    assert len(uncleared["open_issues"]) == 2 and uncleared["withheld"] == 1
    assert uncleared["recent_changes"]["withheld"] == 1
    assert "problems.terms" in uncleared["withheld_facts"]
    hidden = {issue["id"] for issue in cleared["open_issues"]} - {issue["id"] for issue in uncleared["open_issues"]}
    assert hidden and not hidden & set(uncleared["evidence"])
    assert uncleared["markdown"].rstrip().endswith("_1 memory withheld from this brief for your key._")


def test_cautions_are_judged_for_the_keys_agent_profile(client, world):
    profile = client.post(
        f"{world['base']}/agent/profiles",
        json={"name": "support-agent", "readable_types": ["problem", "preference", "subscription"], "denied_actions": ["offer_discount"]},
        headers=world["auth"],
    )
    assert profile.status_code == 201, profile.text
    key = client.post(
        f"{world['base']}/api-keys",
        json={"name": "support", "scopes": SCOPES, "agent_profile_id": profile.json()["id"]},
        headers=world["auth"],
    ).json()["api_key"]
    world["support"] = key
    body = brief(client, world, key="support")
    discount = next(caution for caution in body["cautions"] if caution["action"] == "offer_discount")
    assert discount["decision"] == "deny"
    assert discount["text"] == "Don't offer a discount: the 'support-agent' profile may never take the action 'offer_discount'."
    # The profile reads no intents: the threat is not quoted, though selling is still refused.
    assert not any("will cancel" in point for point in body["talking_points"])
    assert body["intents"] == []
    assert body["cautions"][0]["text"].startswith("Don't offer an upgrade")


def test_the_window_and_its_errors(client, world):
    week = brief(client, world, since="7d")
    assert week["recent_changes"]["window"]["basis"] == "span"
    assert week["recent_changes"]["window"]["label"] == "In the last 7 days"
    bad = client.get("/v1/customers/acme/brief", params={"since": "a while ago"}, headers=h(world))
    assert bad.status_code == 422
    fmt = client.get("/v1/customers/acme/brief", params={"format": "pdf"}, headers=h(world))
    assert fmt.status_code == 422
    missing = client.get("/v1/customers/nobody/brief", headers=h(world))
    assert missing.status_code == 404


def test_a_customer_with_nothing_yet(client, world):
    client.post("/v1/customers", json={"external_id": "newco", "name": "Newco"}, headers=h(world))
    body = brief(client, world, customer="newco")
    assert body["headline"].startswith("Newco: ")
    assert "A new customer." in body["headline"] and body["headline"].endswith("No open problems.")
    assert body["cautions"] == [] and body["open_issues"] == [] and body["next_step"] is None
    window = body["recent_changes"]["window"]
    assert window["basis"] == "last_session" and window["found"] is False and window["note"]
    assert body["markdown"].startswith("# Newco\n")


def test_the_dashboard_sees_the_same_brief(client, world):
    api = brief(client, world)
    dashboard = client.get(f"{world['base']}/customers/acme/brief", headers=world["auth"])
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert body["headline"] == api["headline"]
    assert body["talking_points"] == api["talking_points"]
    assert body["cautions"] == api["cautions"]
    page = client.get(f"{world['base']}/customers/acme/brief", params={"format": "markdown"}, headers=world["auth"])
    assert page.headers["content-type"].startswith("text/markdown") and page.text.startswith("# Acme")
