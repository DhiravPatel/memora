"""The customer journey over HTTP, through the real pipeline (§26 6.6).

Acme signed up ninety days ago and went quiet; forty days ago they came back, connected
Shopify and started using the Campaign Builder; then the Shopify sync failed three times,
they set a goal, downgraded, threatened to cancel, gave a detractor score, and reported a
restricted salary problem. Every event goes through the worker's process_event today, so
health and the lifecycle were judged today — and the journey places each at the event that
caused it.
"""

from __future__ import annotations

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
def world(client: TestClient) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={"email": f"journey-{unique}@example.com", "password": "a-strong-password", "organization_name": f"J {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Journeys"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    applied = client.put(
        f"{base}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    assert applied.status_code == 200, applied.text
    world = {"auth": auth, "base": base, "project_id": project["id"], "events": {}}
    for name, scopes in (("cleared", [*SCOPES, "memory:restricted"]), ("uncleared", SCOPES)):
        world[name] = client.post(f"{base}/api-keys", json={"name": name, "scopes": scopes}, headers=auth).json()["api_key"]

    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    now = datetime.now(UTC)
    world["now"] = now

    def at(days: float) -> datetime:
        return now - timedelta(days=days)

    world["at"] = at
    send(client, world, "signup", "signup", {"company": "Acme", "role": "Head of Ops"}, at=at(90))
    send(client, world, "shopify", "integration_connected", {"integration": "shopify"}, at=at(40))
    for index, days in enumerate((38, 35, 33)):
        send(client, world, f"builder{index}", "feature_used", {"feature": "campaign_builder"}, at=at(days))
    for index, days in enumerate((30, 28, 26)):
        send(client, world, f"sync{index}", "support_message", {"message": "The Shopify sync fails on large orders."}, at=at(days))
    send(client, world, "goal", "goal_set", {"goal": "launch automation"}, at=at(25))
    send(client, world, "downgrade", "subscription_downgraded", {"previous_plan": "pro", "plan": "starter"}, at=at(20))
    send(client, world, "threat", "support_message", {"message": "We will cancel if the Shopify sync keeps failing."}, at=at(18))
    send(client, world, "nps", "nps_submitted", {"score": 4}, at=at(15))
    send(client, world, "salary", "support_message", {"message": "The salary report export fails for payroll."}, at=at(12))
    return world


def h(world: dict, key: str = "cleared") -> dict:
    return {"X-API-Key": world[key]}


def send(client, world, name: str, event_type: str, data: dict, *, at: datetime) -> None:
    sent = client.post(
        "/v1/events",
        json={"customer_id": "acme", "event_type": event_type, "data": data, "occurred_at": at.isoformat()},
        headers=h(world),
    )
    assert sent.status_code == 202, sent.text
    outcome = run_worker(sent.json()["event_id"])
    assert outcome["status"] in ("processed", "skipped"), outcome
    world["events"][name] = (sent.json()["event_id"], at)


def journey(client, world, key: str = "cleared", **params) -> dict:
    response = client.get("/v1/customers/acme/journey", params=params, headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def subsequence(wanted: list[str], titles: list[str]) -> bool:
    remaining = iter(titles)
    return all(any(title == want for title in remaining) for want in wanted)


# ----------------------------------------------------------------------- tests


def test_the_journey_is_milestones_in_the_order_they_happened(client, world):
    body = journey(client, world)
    titles = [item["title"] for item in body["milestones"]]
    assert subsequence(
        [
            "Became a customer",
            "Went quiet",
            "Came back after 7 weeks",
            "Started using Shopify",
            "Started using Campaign Builder",
            "First Shopify problem",
            "Shopify problem reported 3 times",
            "Downgraded from Pro to Starter",
            "Said they may cancel",
            "Negative feedback",
        ],
        titles,
    ), titles
    goal = next(item for item in body["milestones"] if item["category"] == "goal")
    assert goal["title"].startswith("Goal set: “") and "automation" in goal["title"].lower()
    moments = [parse(item["at"]) for item in body["milestones"]]
    assert moments == sorted(moments)
    assert body["window"]["basis"] == "all" and body["summary"].startswith("Customer since ")
    assert body["customer"]["external_id"] == "acme" and body["withheld"] == 0

    first = body["milestones"][0]
    assert first["title"] == "Became a customer" and first["what_happened"] == "Their first recorded event: signup."
    builder = next(item for item in body["milestones"] if item["title"] == "Started using Campaign Builder")
    assert builder["detail"]["uses"] == 3 and builder["why_it_matters"].startswith("Used 3 times since, most recently on ")
    repeated = next(item for item in body["milestones"] if item["title"] == "Shopify problem reported 3 times")
    assert parse(repeated["at"]) == world["events"]["sync2"][1], "placed at the third report"


def test_a_milestone_answers_the_five_questions(client, world):
    body = journey(client, world)
    problem = next(item for item in body["milestones"] if item["title"] == "First Shopify problem")
    assert problem["what_happened"] == "They reported: “The Shopify sync fails on large orders”."
    assert problem["why_it_matters"].startswith("Open problems pull health down until they are resolved; this one is still open after")
    assert problem["topics"] == ["Shopify"] and problem["tone"] == "negative"
    assert [memory["change"] for memory in problem["memories"]][:1] == ["created"]
    assert world["events"]["sync0"][0] in problem["evidence"]["events"]
    health = problem["health"]
    assert health is not None and health["after"]["score"] is not None
    assert health["delta"] is not None and health["delta"] < 0, "the first report cost health"
    assert "1 unresolved problem" in health["drivers"]

    # Health and the lifecycle, judged today, sit at the events that caused them.
    moves = [item for item in body["milestones"] if item["title"] == "Lifecycle → At risk"]
    assert len(moves) == 1, [item["title"] for item in body["milestones"] if item["category"] == "lifecycle"]
    move = moves[0]
    caused_by = {moment for _, moment in world["events"].values()}
    assert parse(move["at"]) in caused_by
    assert move["recorded_at"] is not None and parse(move["recorded_at"]) > parse(move["at"]) + timedelta(days=1)
    assert move["transitions"][0]["after"] == "at_risk" and move["why_it_matters"].startswith("Because ")
    # The milestone whose event tipped it says so.
    tipping = [
        item for item in body["milestones"]
        if item["category"] != "lifecycle" and any(t["after"] == "at_risk" and t["track"] == "lifecycle" for t in item["transitions"])
    ]
    assert tipping and parse(tipping[0]["at"]) == parse(move["at"])
    drops = [item for item in body["milestones"] if item["category"] == "health" and item["kind"] == "dropped"]
    assert drops and all(parse(item["at"]) in caused_by for item in drops)


def test_every_stay_is_tied_to_its_snapshot(client, world, engine):
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT count(*), count(snapshot_id) FROM customer_states WHERE project_id = :p AND source = 'auto'"),
            {"p": world["project_id"]},
        ).one()
    assert rows[0] > 0 and rows[0] == rows[1]


def test_a_reader_without_clearance_is_not_shown_the_restricted_problem(client, world):
    cleared = journey(client, world)
    assert any("salary" in item["what_happened"].lower() for item in cleared["milestones"])
    body = journey(client, world, key="uncleared")
    assert body["withheld"] >= 1
    assert "salary" not in str(body).lower()
    assert len(body["milestones"]) < len(cleared["milestones"])


def test_filters_windows_and_limits(client, world):
    problems = journey(client, world, categories="problem")
    assert problems["milestones"] and {item["category"] for item in problems["milestones"]} == {"problem"}
    important = journey(client, world, min_importance=0.85)
    assert all(item["importance"] >= 0.85 for item in important["milestones"])
    limited = journey(client, world, limit=3)
    assert len(limited["milestones"]) == 3 and limited["truncated"] is True and limited["total"] > 3
    recent = journey(client, world, since="21d")
    cutoff = world["now"] - timedelta(days=21, minutes=5)
    assert recent["milestones"] and all(parse(item["at"]) >= cutoff for item in recent["milestones"])
    assert recent["window"]["basis"] == "span"
    bad = client.get("/v1/customers/acme/journey", params={"categories": "problems"}, headers=h(world))
    assert bad.status_code == 422 and "Unknown milestone category" in bad.json()["error"]["message"]
    assert client.get("/v1/customers/nobody/journey", headers=h(world)).status_code == 404


def test_the_page_and_the_dashboard(client, world):
    page = client.get("/v1/customers/acme/journey", params={"format": "markdown"}, headers=h(world))
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/markdown")
    assert page.text.startswith("# Acme — journey\n") and "\n## " in page.text
    assert "First Shopify problem" in page.text

    api = journey(client, world)
    dashboard = client.get(f"{world['base']}/customers/acme/journey", headers=world["auth"])
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert [item["id"] for item in body["milestones"]] == [item["id"] for item in api["milestones"]]
    assert body["summary"] == api["summary"]
