"""What changed, over HTTP, through the real pipeline (§26 4.1).

Events from three weeks ago establish who the customer was; events from the last few days
change it. Both go through the worker's process_event — extraction, consolidation, the
restriction policy, the lifecycle — so supersession and resolution are the pipeline's own,
not staged. The only thing staged is time: the snapshots and lifecycle stays the old
events produced are moved back to when those events happened.
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
OLD = timedelta(days=20)


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
        json={"email": f"changes-{unique}@example.com", "password": "a-strong-password", "organization_name": f"C {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Changes"}, headers=auth).json()
    applied = client.put(
        f"/v1/projects/{project['id']}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    assert applied.status_code == 200, applied.text
    keys = {}
    for name, scopes in (("cleared", [*SCOPES, "memory:restricted"]), ("uncleared", SCOPES)):
        created = client.post(
            f"/v1/projects/{project['id']}/api-keys", json={"name": name, "scopes": scopes}, headers=auth
        )
        keys[name] = created.json()["api_key"]
    world = {"auth": auth, "project_id": project["id"], **keys}

    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    then = datetime.now(UTC) - OLD
    send(client, world, "subscription_changed", {"plan": "starter"}, at=then)
    send(client, world, "support_message", {"message": "The payroll export fails every night."}, at=then + timedelta(minutes=1))
    send(client, world, "preferences_updated", {"message": "We prefer to be contacted by email."}, at=then + timedelta(minutes=2))
    # Everything those events produced happened then, not now.
    with engine.begin() as connection:
        for table, column in (("customer_snapshots", "taken_at"), ("customer_states", "entered_at"), ("customer_states", "exited_at")):
            connection.execute(
                text(f"UPDATE {table} SET {column} = {column} - make_interval(days => 20) WHERE project_id = :p AND {column} IS NOT NULL"),
                {"p": world["project_id"]},
            )
        connection.execute(
            text(
                "UPDATE memory_versions SET created_at = created_at - make_interval(days => 20) "
                "WHERE memory_id IN (SELECT id FROM memories WHERE project_id = :p)"
            ),
            {"p": world["project_id"]},
        )

    recent = datetime.now(UTC) - timedelta(days=2)
    send(client, world, "subscription_upgraded", {"previous_plan": "starter", "plan": "pro"}, at=recent)
    send(client, world, "support_message", {"message": "The payroll export works again, thanks for fixing it."}, at=recent + timedelta(hours=1))
    send(client, world, "preferences_updated", {"message": "We prefer WhatsApp now."}, at=recent + timedelta(hours=2))
    send(client, world, "cancellation_requested", {"reason": "invoices keep failing"}, at=recent + timedelta(hours=3))
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


def changes(client, world, key: str = "cleared", **params) -> dict:
    response = client.get("/v1/customers/acme/changes", params=params, headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


def by_kind(body: dict) -> dict[tuple[str, str], list[dict]]:
    found: dict[tuple[str, str], list[dict]] = {}
    for change in body["changes"]:
        found.setdefault((change["type"], change["kind"]), []).append(change)
    return found


# ----------------------------------------------------------------------- tests


def test_what_changed_in_the_last_week(client, world):
    body = changes(client, world, since="7d")
    found = by_kind(body)
    assert body["window"]["basis"] == "span" and body["window"]["label"] == "In the last 7 days"

    plan = found[("subscription", "changed")][0]
    assert plan["title"] == "Upgraded from Starter to Pro"
    assert plan["before"] == "The customer changed to the Starter plan."
    assert plan["detail"]["previous_plan"] == "starter"

    resolved = found[("problem", "resolved")][0]
    assert "payroll export" in resolved["after"].lower()
    assert resolved["before"] == "The payroll export fails every night."

    preference = found[("preference", "changed")][0]
    assert "whatsapp" in preference["after"].lower()
    assert "email" in preference["before"].lower()

    assert found[("intent", "expressed")][0]["detail"]["kinds"] == ["cancellation"]
    assert any("salary" in change["after"] for change in found[("problem", "opened")])

    # Nothing from three weeks ago is reported as happening this week.
    assert all("fails every night" not in (change["after"] or "") for change in body["changes"])
    assert body["summary"].startswith("In the last 7 days: upgraded from Starter to Pro;")


def test_then_and_now(client, world):
    body = changes(client, world, since="7d")
    then, now = body["then"], body["now"]
    assert then["live"] is False and then["snapshot_id"]
    assert then["state"]["plan"] == "starter"
    assert now["live"] is True and now["state"]["plan"] == "pro"
    assert now["state"]["preferred_channel"] == "whatsapp"
    assert "Pro plan" in now["description"]


def test_lifecycle_moves_in_the_window_carry_reasons(client, world):
    body = changes(client, world, since="7d", types="lifecycle")
    assert body["changes"], body
    assert {change["type"] for change in body["changes"]} == {"lifecycle"}
    commercial = [change for change in body["changes"] if change["track"] == "commercial"]
    assert commercial and commercial[0]["after"] == "expanding"
    assert "subscription upgraded" in commercial[0]["reasons"]


def test_a_reader_without_clearance_is_told_what_was_withheld(client, world):
    body = changes(client, world, "uncleared", since="7d")
    blob = json.dumps(body).lower()
    assert "salary" not in blob and "stripe" not in blob
    assert body["withheld"] >= 1
    assert ("subscription", "changed") in by_kind(body)


def test_since_the_last_conversation(client, world):
    fallback = changes(client, world, since="last_session")
    assert fallback["window"]["basis"] == "last_session"
    assert fallback["window"]["found"] is False
    assert "No earlier conversation" in fallback["window"]["note"]

    opened = client.post("/v1/agent/sessions", json={"customer_id": "acme", "agent": "support-bot"}, headers=h(world))
    assert opened.status_code == 201, opened.text
    closed = client.post(f"/v1/agent/sessions/{opened.json()['id']}/close", json={"write_summary": False}, headers=h(world))
    assert closed.status_code == 200, closed.text
    send(client, world, "support_message", {"message": "SSO login loops back to the start page."}, at=datetime.now(UTC))

    body = changes(client, world, since="last_session", agent="support-bot")
    assert body["window"]["found"] is True and body["window"]["value"] == opened.json()["id"]
    assert body["window"]["label"].startswith("Since the last conversation with support-bot")
    titles = [change["title"] for change in body["changes"]]
    assert any("SSO login" in title for title in titles)
    assert not any("Upgraded" in title for title in titles), "the upgrade was before the conversation"


def test_compare_two_moments(client, world):
    response = client.get("/v1/customers/acme/compare", params={"from": "10d"}, headers=h(world))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["then"]["state"]["plan"] == "starter"
    assert body["now"]["state"]["plan"] == "pro"
    facts = {entry["fact"] for entry in body["differences"]}
    assert {"subscription.plan", "preferences.channel"} <= facts
    assert body["summary"].startswith("Then: ")

    nothing = client.get("/v1/customers/acme/compare", params={"from": "90d"}, headers=h(world)).json()
    assert nothing["then"]["state"] is None and nothing["differences"] == []


def test_the_window_can_end_in_the_past(client, world):
    snapshots = client.get("/v1/customers/acme/snapshots", params={"limit": 200}, headers=h(world)).json()["data"]
    oldest = snapshots[-1]
    body = changes(client, world, since="30d", until=oldest["id"])
    assert body["now"]["live"] is False
    assert body["now"]["snapshot_id"] == oldest["id"]
    assert all("Upgraded" not in change["title"] for change in body["changes"])


def test_filters_and_order(client, world):
    problems = changes(client, world, since="7d", types="problem")
    assert problems["changes"] and {change["type"] for change in problems["changes"]} == {"problem"}
    ranked = changes(client, world, since="7d", order="importance", limit=3)
    assert len(ranked["changes"]) == 3 and ranked["truncated"] is True
    importances = [change["importance"] for change in ranked["changes"]]
    assert importances == sorted(importances, reverse=True)


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"since": "yesterday-ish"}, 422),
        ({"since": "0d"}, 422),
        ({"since": "99999d"}, 422),
        ({"since": "7d", "types": "problem,weather"}, 422),
        ({"since": "snp_nope"}, 404),
        ({"since": "2999-01-01"}, 422),
    ],
)
def test_bad_windows_are_refused(client, world, params, status):
    response = client.get("/v1/customers/acme/changes", params=params, headers=h(world))
    assert response.status_code == status, response.text


def test_the_dashboard_sees_the_same_changes(client, world):
    response = client.get(
        f"/v1/projects/{world['project_id']}/customers/acme/changes", params={"since": "7d"}, headers=world["auth"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == changes(client, world, since="7d")["summary"]


def test_changes_name_their_topics(client, world):
    body = changes(client, world, since="7d")
    salary = next(change for change in body["changes"] if "Stripe payout" in change["title"])
    assert salary["topics"] == ["Stripe"]


def test_since_i_last_looked(client, world, engine):
    """``last_view``: since this person — or this agent's key — last looked at the customer.
    Loads within half an hour are one visit, so it means the visit before this one."""
    never = changes(client, world, since="last_view")
    assert never["window"]["basis"] == "last_view" and never["window"]["found"] is False
    assert never["window"]["note"] == "You have not looked at this customer before; showing the last 30 days instead."

    # An agent's key looks by reading the brief or the 360.
    assert client.get("/v1/customers/acme/brief", headers=h(world)).status_code == 200
    with engine.begin() as connection:
        connection.execute(text("UPDATE customer_views SET viewed_at = now() - interval '3 days'"))
    looked = changes(client, world, since="last_view")
    assert looked["window"]["basis"] == "last_view" and looked["window"]["found"] is True
    assert looked["window"]["label"].startswith("Since you last looked (")
    assert all(change["detected_at"] >= looked["window"]["since"] for change in looked["changes"])

    # A person looks by opening the customer; the visit under way does not count.
    base = f"/v1/projects/{world['project_id']}"
    assert client.get(f"{base}/customers/acme", headers=world["auth"]).status_code == 200
    first = client.get(f"{base}/customers/acme/changes", params={"since": "last_view"}, headers=world["auth"]).json()
    assert first["window"]["found"] is False, "the first visit has nothing before it"
    with engine.begin() as connection:
        connection.execute(text("UPDATE customer_views SET viewed_at = now() - interval '2 days' WHERE viewer_type = 'user'"))
    assert client.get(f"{base}/customers/acme", headers=world["auth"]).status_code == 200  # a new visit
    again = client.get(f"{base}/customers/acme/changes", params={"since": "last_view"}, headers=world["auth"]).json()
    assert again["window"]["found"] is True and again["window"]["basis"] == "last_view"
    # Peeking without counting as a look is possible.
    assert client.get(f"{base}/customers/acme", params={"look": "false"}, headers=world["auth"]).status_code == 200
