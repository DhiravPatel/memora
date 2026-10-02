"""Personalization over HTTP, through the real pipeline (§26 6.6).

Acme signed up sixty days ago, connected Shopify, uses the Campaign Builder most days, said
they prefer email, set a goal, and this week reported a Shopify sync failure and a
restricted salary problem. Every event goes through the worker's process_event, whose state
refresh computes and stores the personalization a product then reads.
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
        json={"email": f"personal-{unique}@example.com", "password": "a-strong-password", "organization_name": f"P {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Personal"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    applied = client.put(
        f"{base}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    assert applied.status_code == 200, applied.text
    world = {"auth": auth, "base": base, "project_id": project["id"]}
    for name, scopes in (
        ("cleared", [*SCOPES, "memory:restricted"]),
        ("uncleared", SCOPES),
        ("narrow", ["personalization:read"]),
        ("writer", ["events:write"]),
    ):
        created = client.post(f"{base}/api-keys", json={"name": name, "scopes": scopes}, headers=auth)
        assert created.status_code in (200, 201), created.text
        world[name] = created.json()["api_key"]
    client.post(
        f"{base}/webhooks",
        json={"url": "https://hooks.example.com/personal", "event_types": ["customer.personalization_changed"]},
        headers=auth,
    )

    now = datetime.now(UTC)
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    send(client, world, "signup", {"company": "Acme", "role": "Head of Ops"}, at=now - timedelta(days=60))
    send(client, world, "integration_connected", {"integration": "shopify"}, at=now - timedelta(days=19))
    for days in (20, 3, 2, 1):
        send(client, world, "feature_used", {"feature": "campaign_builder"}, at=now - timedelta(days=days))
    send(client, world, "preferences_updated", {"message": "We prefer email."}, at=now - timedelta(days=15))
    send(client, world, "goal_set", {"goal": "launch automation"}, at=now - timedelta(days=10))
    send(client, world, "support_message", {"message": "The Shopify sync fails on large orders."}, at=now - timedelta(days=2))
    send(client, world, "support_message", {"message": "The salary report export fails for payroll."}, at=now - timedelta(days=1))
    return world


def h(world: dict, key: str = "cleared") -> dict:
    return {"X-API-Key": world[key]}


def send(client, world, event_type: str, data: dict, *, at: datetime) -> dict:
    sent = client.post(
        "/v1/events",
        json={"customer_id": "acme", "event_type": event_type, "data": data, "occurred_at": at.isoformat()},
        headers=h(world),
    )
    assert sent.status_code == 202, sent.text
    outcome = run_worker(sent.json()["event_id"])
    assert outcome["status"] in ("processed", "skipped"), outcome
    return outcome


def personalization(client, world, key: str = "cleared", **params) -> dict:
    response = client.get("/v1/customers/acme/personalization", params=params, headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


def salary_memory(client, world) -> str:
    listed = client.get("/v1/customers/acme/memories", params={"type": "problem"}, headers=h(world)).json()["data"]
    return next(item["id"] for item in listed if "salary" in item["content"].lower())


def deliveries(client, world) -> list[dict]:
    rows = client.get(f"{world['base']}/webhooks/deliveries", params={"limit": 100}, headers=world["auth"]).json()["data"]
    return [row["payload"]["data"] for row in rows if row["event_type"] == "customer.personalization_changed"]


# ----------------------------------------------------------------------- tests


def test_it_is_computed_as_events_arrive_and_read_from_the_store(client, world, engine):
    with engine.begin() as connection:
        stored = connection.execute(
            text("SELECT reason FROM customer_personalizations WHERE project_id = :p"), {"p": world["project_id"]}
        ).all()
    assert [row[0] for row in stored] == ["event"], "the worker's refresh stored it, before anyone read it"

    body = personalization(client, world)
    assert body["customer_id"] == "acme"
    assert body["preferred_channel"] == "email"
    assert body["current_goal"] and "automation" in body["current_goal"]
    assert "shopify" in body["known_frictions"]
    assert {"campaign_builder", "shopify"} <= set(body["features_used"])
    assert body["relied_on_features"] == ["campaign_builder"], "3 uses in the last 30 days"
    assert body["experience"] != "new", "their history began sixty days ago, whatever the record says"
    assert body["ui"]["offer_help"] is True and body["ui"]["suppress_upsell"] is True
    upsell = body["details"]["ui"]["suppress_upsell"]
    assert any("open problem" in reason for reason in upsell["because"]), upsell
    friction = next(item for item in body["details"]["known_frictions"] if item["key"] == "shopify")
    assert friction["mode"] == "broken" and friction["label"] == "Shopify"


def test_restricted_memories_never_reach_it(client, world):
    secret = salary_memory(client, world)
    for key in ("cleared", "uncleared"):
        body = personalization(client, world, key)
        assert secret not in body["evidence"]
        assert "salary" not in str(body).lower()
        assert all(secret not in item["memory_ids"] for item in body["details"]["known_frictions"])
    # The counts stay whole — numbers about the customer, not quotes from them.
    assert personalization(client, world)["details"]["ui"]["offer_help"]["value"] is True


def test_an_etag_answers_304_while_nothing_changed(client, world):
    first = client.get("/v1/customers/acme/personalization", headers=h(world))
    etag = first.headers["etag"]
    assert etag.startswith('W/"') and first.headers["cache-control"] == "private, max-age=30"
    again = client.get("/v1/customers/acme/personalization", headers={**h(world), "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    flat = client.get("/v1/customers/acme/personalization", params={"details": "false"}, headers=h(world))
    assert flat.headers["etag"] != etag and "details" not in flat.json()


def test_a_narrow_key_reads_personalization_and_nothing_else(client, world):
    assert client.get("/v1/customers/acme/personalization", headers=h(world, "narrow")).status_code == 200
    assert client.post(
        "/v1/personalization/batch", json={"customer_ids": ["acme"]}, headers=h(world, "narrow")
    ).status_code == 200
    assert client.get("/v1/customers/acme", headers=h(world, "narrow")).status_code == 403
    assert client.get("/v1/customers/acme/memories", headers=h(world, "narrow")).status_code == 403
    refused = client.get("/v1/customers/acme/personalization", headers=h(world, "writer"))
    assert refused.status_code == 403 and "personalization:read" in refused.json()["error"]["message"]


def test_many_customers_at_once(client, world):
    response = client.post(
        "/v1/personalization/batch", json={"customer_ids": ["acme", "nobody", "acme"]}, headers=h(world)
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [(row["customer_id"], row["error"]) for row in rows] == [("acme", None), ("nobody", "not_found")]
    assert rows[0]["personalization"]["details"] is None and rows[0]["personalization"]["ui"]
    too_many = client.post(
        "/v1/personalization/batch", json={"customer_ids": [f"c{n}" for n in range(51)]}, headers=h(world)
    )
    assert too_many.status_code == 422


def test_a_change_is_a_webhook(client, world):
    before = personalization(client, world)
    send(client, world, "preferences_updated", {"message": "Please contact us on WhatsApp instead of email."}, at=datetime.now(UTC))
    after = personalization(client, world)
    assert after["preferred_channel"] == "whatsapp" and after["version"] != before["version"]
    changed = deliveries(client, world)
    assert changed, "the change was announced"
    latest = changed[0]
    assert {"field": "preferred_channel", "before": "email", "after": "whatsapp"} in latest["changes"]
    assert latest["customer"]["external_id"] == "acme"
    assert latest["personalization"]["preferred_channel"] == "whatsapp" and "details" not in latest["personalization"]


def test_writes_that_are_not_events_move_it_at_once(client, world, engine):
    """A memory written by hand and feedback on one refresh the customer there and then."""
    written = client.post(
        "/v1/memories",
        json={"customer_id": "acme", "type": "preference", "content": "Please do not call us."},
        headers=h(world),
    )
    assert written.status_code == 201, written.text
    assert personalization(client, world)["opt_outs"] == ["phone"]

    shopify = next(
        item["id"]
        for item in client.get("/v1/customers/acme/memories", params={"type": "problem"}, headers=h(world)).json()["data"]
        if "Shopify" in item["content"]
    )
    before = personalization(client, world)["computed_at"]
    # Rejecting lowers confidence — the problem stays open, and so does the friction.
    rejected = client.post(f"/v1/memories/{shopify}/feedback", json={"verdict": "reject"}, headers=h(world))
    assert rejected.status_code == 200, rejected.text
    with engine.begin() as connection:
        reason = connection.execute(
            text("SELECT reason FROM customer_personalizations WHERE project_id = :p"), {"p": world["project_id"]}
        ).scalar_one()
    assert reason == "feedback", "recomputed by the refresh the feedback ran, not by the read"
    after = personalization(client, world)
    assert after["computed_at"] > before and "shopify" in after["known_frictions"]


def test_the_projects_rules_decide_the_hints(client, world):
    refused = client.put(
        f"{world['base']}/settings",
        json={"settings": {"personalization": {"hints": {"beta_invite": {"when": "plan.name == 1"}}}}},
        headers=world["auth"],
    )
    assert refused.status_code == 422 and "Unknown fact 'plan.name'" in refused.json()["error"]["message"]

    applied = client.put(
        f"{world['base']}/settings",
        json={
            "settings": {
                "personalization": {
                    "hints": {
                        "beta_invite": {"when": 'personalization.experience != "new"', "description": "Invite to the beta."},
                        "offer_help": {"enabled": False},
                    }
                }
            }
        },
        headers=world["auth"],
    )
    assert applied.status_code == 200, applied.text
    body = personalization(client, world)
    assert body["ui"]["beta_invite"] is True and "offer_help" not in body["ui"]
    assert body["details"]["ui"]["beta_invite"]["custom"] is True
    rules = client.get("/v1/personalization/rules", headers=h(world)).json()
    assert "beta_invite" in {hint["key"] for hint in rules["hints"]}
    assert "offer_help" in rules["builtin_hints"] and rules["defaults"]["relied_on_uses"] == 3


def test_a_preview_tries_rules_without_saving_them(client, world):
    stored = personalization(client, world)["version"]
    preview = client.post(
        f"{world['base']}/personalization/preview",
        json={"customer_id": "acme", "rules": {"hints": {"only_this": {"when": "problems.open_count >= 1"}}}},
        headers=world["auth"],
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["ui"]["only_this"] is True
    assert personalization(client, world)["version"] == stored, "nothing saved"
    bad = client.post(
        f"{world['base']}/personalization/preview",
        json={"customer_id": "acme", "rules": {"hints": {"x": {"when": "health.score > 1"}}}},
        headers=world["auth"],
    )
    assert bad.status_code == 422


def test_the_dashboard_and_the_summary(client, world):
    api = personalization(client, world)
    dashboard = client.get(f"{world['base']}/customers/acme/personalization", headers=world["auth"]).json()
    assert dashboard["version"] == api["version"] and dashboard["ui"] == api["ui"]
    recomputed = client.post(f"{world['base']}/customers/acme/personalization/refresh", headers=world["auth"])
    assert recomputed.status_code == 200 and recomputed.json()["version"] == api["version"]
    summary = client.get(f"{world['base']}/personalization/summary", headers=world["auth"]).json()
    assert summary["customers"] == 1
    assert summary["hints"]["beta_invite"] == 1 and summary["hints"]["suppress_upsell"] == 1
    assert {"key": "shopify", "customers": 1} in summary["frictions"]
    assert summary["relied_on_features"] == [{"key": "campaign_builder", "customers": 1}]
