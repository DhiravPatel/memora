"""Freshness and drift over HTTP, through the real pipeline (§26 5.5).

Sixty days ago Acme said they prefer email, were put on the Pro plan and used the Campaign
Builder; fifty days ago they reported a CSV export timing out. Since then they have reached
out six times on WhatsApp and once by email, been billed twice for Enterprise, and not used
the Campaign Builder again — while staying active. Every event goes through the worker's
process_event: the event path flags what an event can move (the channel, the plan) and the
refresh — the nightly sweep's detector — flags what only time moves.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required, run_job, run_worker

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
        json={"email": f"drift-{unique}@example.com", "password": "a-strong-password", "organization_name": f"D {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Drift"}, headers=auth).json()
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
    client.post(
        f"{base}/webhooks",
        json={"url": "https://hooks.example.com/drift", "event_types": ["memory.drift_detected", "memory.drift_resolved"]},
        headers=auth,
    )

    now = datetime.now(UTC)
    then = now - timedelta(days=60)
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    world["results"] = {}
    send(client, world, "preferences_updated", {"message": "We prefer email."}, at=then)
    send(client, world, "subscription_changed", {"plan": "pro"}, at=then + timedelta(minutes=1))
    send(client, world, "feature_used", {"feature": "campaign_builder"}, at=then - timedelta(days=10))
    send(client, world, "support_message", {"message": "The CSV export times out on large files."}, at=now - timedelta(days=50))
    send(client, world, "support_message", {"message": "The salary report export fails for payroll."}, at=now - timedelta(days=45))
    for day in range(6):
        world["results"][f"wa{day}"] = send(
            client, world, "whatsapp_message", {"message": "Hi, any news?"}, at=now - timedelta(days=9 - day)
        )
    send(client, world, "email_received", {"message": "Following up by email."}, at=now - timedelta(days=2))
    for day in (20, 5):
        world["results"][f"invoice{day}"] = send(
            client, world, "invoice_paid", {"plan": "Enterprise", "amount": 900}, at=now - timedelta(days=day)
        )
    return world


def h(world: dict, key: str = "cleared") -> dict:
    return {"X-API-Key": world[key]}


def send(client, world, event_type: str, data: dict, *, at: datetime, customer: str = "acme") -> dict:
    sent = client.post(
        "/v1/events",
        json={"customer_id": customer, "event_type": event_type, "data": data, "occurred_at": at.isoformat()},
        headers=h(world),
    )
    assert sent.status_code == 202, sent.text
    outcome = run_worker(sent.json()["event_id"])
    assert outcome["status"] in ("processed", "skipped"), outcome
    return {"event_id": sent.json()["event_id"], **outcome}


def flags(client, world, key: str = "cleared", **params) -> dict:
    response = client.get("/v1/drift", params={"customer_id": "acme", **params}, headers=h(world, key))
    assert response.status_code == 200, response.text
    return response.json()


def deliveries(client, world, event_type: str) -> list[dict]:
    rows = client.get(f"{world['base']}/webhooks/deliveries", params={"limit": 100}, headers=world["auth"]).json()["data"]
    return [row["payload"]["data"] for row in rows if row["event_type"] == event_type]


def by_kind(body: dict) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for flag in body["data"]:
        found.setdefault(flag["kind"], []).append(flag)
    return found


# ----------------------------------------------------------------------- tests


def test_the_event_path_flags_a_changed_channel_and_plan(client, world):
    body = flags(client, world)
    found = by_kind(body)
    assert set(found) == {"channel", "plan"}, "only what events move is flagged on the event path"

    channel = found["channel"][0]
    assert channel["stated"] == "email" and channel["observed"] == "WhatsApp"
    assert channel["counts"] == {"by_channel": {"WhatsApp": 6, "email": 1}, "total": 7, "observed": 6, "stated": 1}
    assert channel["summary"].startswith("They said they prefer email on ")
    assert channel["summary"].endswith("since then 6 of their 7 contacts came through WhatsApp and 1 through email.")
    whatsapp = [world["results"][f"wa{day}"]["event_id"] for day in range(6)]
    assert channel["evidence"] == list(reversed(whatsapp)), "the contacts behind it, newest first"
    assert channel["memory"]["content"] == "The customer prefers email." and channel["memory"]["type"] == "preference"

    plan = found["plan"][0]
    assert (plan["stated"], plan["observed"]) == ("pro", "enterprise")
    assert plan["counts"]["events"] == 2
    assert plan["evidence"] == [world["results"]["invoice5"]["event_id"], world["results"]["invoice20"]["event_id"]]
    assert "their last 2 billing events were for the Enterprise plan" in plan["summary"]

    # Opened by the fifth WhatsApp contact, as it was processed — not before.
    assert world["results"]["wa3"]["drift"]["opened"] == []
    assert world["results"]["wa4"]["drift"]["opened"] == [channel["id"]]
    detected = deliveries(client, world, "memory.drift_detected")
    assert {item["drift"]["kind"] for item in detected} == {"channel", "plan"}
    assert detected[0]["customer"]["external_id"] == "acme"


def test_what_only_time_moves_is_found_by_the_sweep(client, world):
    run = client.post("/v1/customers/acme/drift/refresh", headers=h(world))
    assert run.status_code == 200, run.text
    body = run.json()
    assert len(body["opened"]) == 3 and len(body["refreshed"]) == 2 and body["cleared"] == []
    found = {flag["kind"]: flag for flag in body["open"]}
    assert set(found) == {"channel", "plan", "usage", "quiet_problem"}
    usage = found["usage"]
    assert usage["stated"] == "Campaign Builder" and usage["counts"]["quiet_days"] == 70
    assert usage["summary"].startswith("No Campaign Builder use in 2 months (last on ")
    changes = client.get("/v1/customers/acme/changes", params={"since": "1d"}, headers=h(world)).json()
    outdated = [item for item in changes["changes"] if (item["type"], item["kind"]) == ("memory", "outdated")]
    assert len(outdated) == 5 and "5 memories may be out of date" in changes["summary"]
    quiet = [flag for flag in body["open"] if flag["kind"] == "quiet_problem"]
    assert {flag["stated"] for flag in quiet} == {
        "The CSV export times out on large files.",
        "The salary report export fails for payroll.",
    }
    assert all("it may have been fixed" in flag["summary"] for flag in quiet)

    # The nightly job finds the same: nothing new opens, and it says so.
    from worker.tasks.drift import detect_drift

    swept = run_job(detect_drift, world["project_id"])
    assert swept["scanned"] == 1 and swept["opened"] == 0 and swept["failed"] == 0
    assert len(deliveries(client, world, "memory.drift_detected")) == 5


def test_freshness_says_how_current_each_memory_is(client, world):
    report = client.get("/v1/customers/acme/freshness", headers=h(world)).json()
    assert report["counts"]["outdated"] == 5 and report["needs_attention"] == 5
    by_content = {item["content"]: item["freshness"] for item in report["memories"]}
    preference = by_content["The customer prefers email."]
    assert preference["state"] == "outdated" and preference["window_days"] == 180
    assert preference["reasons"][0].startswith("They said they prefer email on ")
    assert preference["effective_confidence"] < 0.9 * 0.6
    assert {flag["kind"] for flag in report["drift"]} == {"channel", "plan", "usage", "quiet_problem"}
    assert report["windows"]["problem"] == 30 and report["windows"]["fact"] == 365

    listed = client.get("/v1/customers/acme/memories", params={"type": "preference"}, headers=h(world)).json()
    assert listed["data"][0]["freshness"]["state"] == "outdated"
    one = client.get(f"/v1/memories/{listed['data'][0]['id']}", headers=h(world)).json()
    assert one["freshness"]["drift"][0]["kind"] == "channel"


def test_a_reader_without_clearance_sees_only_what_it_may(client, world):
    cleared = flags(client, world, status="open")
    salary = next(flag for flag in cleared["data"] if "salary" in flag["stated"])
    world["salary_flag"] = salary["id"]
    uncleared = flags(client, world, key="uncleared", status="open")
    assert uncleared["withheld"] == 1 and uncleared["total"] == cleared["total"] - 1
    assert "salary" not in str(uncleared).lower()
    assert client.get(f"/v1/drift/{salary['id']}", headers=h(world, "uncleared")).status_code == 404
    report = client.get("/v1/customers/acme/freshness", headers=h(world, "uncleared")).json()
    assert report["withheld"] >= 1 and "salary" not in str(report).lower()


def test_facts_guardrails_the_brief_and_context_say_so(client, world):
    facts = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]
    assert facts["preferences.channel"] == "email", "never silently changed"
    assert facts["preferences.channel_outdated"] is True
    assert facts["preferences.observed_channel"] == "whatsapp"
    assert facts["preferences.observed_share"] == 0.857
    assert facts["drift.open_count"] == 5
    assert facts["drift.kinds"] == ["channel", "plan", "usage", "quiet_problem"]
    assert facts["memories.attention_count"] >= 5

    check = client.post(
        "/v1/agent/check",
        json={"customer_id": "acme", "action": "contact_customer", "request": {"channel": "whatsapp"}},
        headers=h(world),
    ).json()
    assert check["decision"] == "deny"
    reason = next(item for item in check["reasons"] if item["rule"] == "channel_preference")
    assert reason["explanation"] == (
        "The customer prefers email, not whatsapp — though 86% of their contacts since came through "
        "WhatsApp; a person can confirm the change."
    )

    brief = client.get("/v1/customers/acme/brief", params={"since": "30d"}, headers=h(world)).json()
    assert "They said they prefer email, but 6 of their 7 contacts since came through WhatsApp — ask which they prefer now." in brief["talking_points"]
    assert {flag["kind"] for flag in brief["drift"]} == {"channel", "plan", "usage", "quiet_problem"}
    assert brief["preferences"]["channel_outdated"] is True and brief["preferences"]["observed_channel"] == "WhatsApp"
    assert "## Possibly out of date" in brief["markdown"]
    assert not any(point.startswith("Still open") and "CSV export" in point for point in brief["talking_points"]), (
        "a problem drift says may be fixed is asked about, not quoted as still open"
    )

    context = client.post(
        "/v1/memory/context",
        json={"customer_id": "acme", "task": "how to contact them", "format": "text"},
        headers=h(world),
    ).json()
    assert "The customer prefers email. (possibly outdated: They said they prefer email on " in context["prompt_text"]
    prefers = next(item for item in context["customer_context"]["memories"] if item["content"] == "The customer prefers email.")
    assert prefers["freshness"] == "outdated" and prefers["effective_confidence"] < prefers["confidence"]


def test_confirming_writes_the_change_through_the_normal_path(client, world):
    found = by_kind(flags(client, world))
    confirmed = client.post(
        f"/v1/drift/{found['channel'][0]['id']}/confirm", json={"note": "They told us on WhatsApp."}, headers=h(world)
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed" and body["note"] == "They told us on WhatsApp." and body["resolved_by_type"] == "api_key"
    replacement = client.get(f"/v1/memories/{body['replacement_memory_id']}", headers=h(world)).json()
    assert replacement["content"] == "The customer prefers WhatsApp." and replacement["source"] == "manual"
    assert replacement["freshness"]["state"] == "active"
    old = client.get(f"/v1/memories/{found['channel'][0]['memory']['id']}", headers=h(world)).json()
    assert old["status"] == "superseded" and old["versions"][0]["reason"] == "drift_confirmed"

    facts = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]
    assert facts["preferences.channel"] == "whatsapp" and facts["preferences.channel_outdated"] is False
    check = client.post(
        "/v1/agent/check",
        json={"customer_id": "acme", "action": "contact_customer", "request": {"channel": "whatsapp"}},
        headers=h(world),
    ).json()
    assert not any(item["rule"] == "channel_preference" for item in check["reasons"])

    again = client.post(f"/v1/drift/{found['channel'][0]['id']}/confirm", json={}, headers=h(world))
    assert again.status_code == 409

    plan = client.post(f"/v1/drift/{found['plan'][0]['id']}/confirm", json={}, headers=h(world)).json()
    written = client.get(f"/v1/memories/{plan['replacement_memory_id']}", headers=h(world)).json()
    assert written["content"] == "The customer upgraded from the Pro plan to the Enterprise plan."
    facts = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]
    assert facts["subscription.plan"] == "enterprise" and facts["subscription.direction"] == "upgraded"

    before = facts["problems.open_count"]
    csv = next(flag for flag in found["quiet_problem"] if "CSV" in flag["stated"])
    resolved = client.post(f"/v1/drift/{csv['id']}/confirm", json={}, headers=h(world)).json()
    fixed = client.get(f"/v1/memories/{resolved['replacement_memory_id']}", headers=h(world)).json()
    assert fixed["type"] == "fact" and fixed["content"].startswith("The problem “The CSV export times out on large files” is resolved")
    facts = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]
    assert facts["problems.open_count"] == before - 1
    changes = client.get("/v1/customers/acme/changes", params={"since": "1d"}, headers=h(world)).json()
    assert ("problem", "resolved") in {(item["type"], item["kind"]) for item in changes["changes"]}

    resolved_events = deliveries(client, world, "memory.drift_resolved")
    assert {item["drift"]["status"] for item in resolved_events} == {"confirmed"}


def test_dismissing_keeps_the_memory_and_waits_for_new_evidence(client, world):
    usage = by_kind(flags(client, world))["usage"][0]
    dismissed = client.post(f"/v1/drift/{usage['id']}/dismiss", json={"note": "Seasonal."}, headers=h(world)).json()
    assert dismissed["status"] == "dismissed" and dismissed["replacement_memory_id"] is None
    memory = client.get(f"/v1/memories/{usage['memory']['id']}", headers=h(world)).json()
    assert memory["status"] == "active"
    run = client.post("/v1/customers/acme/drift/refresh", headers=h(world)).json()
    assert "usage" not in {flag["kind"] for flag in run["open"]}, "counting restarts at the dismissal"
    listed = flags(client, world, status="dismissed")
    assert [flag["id"] for flag in listed["data"]] == [usage["id"]]


def test_feedback_on_the_memory_settles_its_flags(client, world):
    salary = client.get(f"/v1/drift/{world['salary_flag']}", headers=h(world)).json()
    confirmed = client.post(
        f"/v1/memories/{salary['memory']['id']}/feedback", json={"verdict": "confirm"}, headers=h(world)
    )
    assert confirmed.status_code == 200, confirmed.text
    settled = client.get(f"/v1/drift/{world['salary_flag']}", headers=h(world)).json()
    assert settled["status"] == "dismissed" and settled["note"] == "A person confirmed the memory."
    memory = client.get(f"/v1/memories/{salary['memory']['id']}", headers=h(world)).json()
    assert memory["freshness"]["state"] == "active", "a person vouching for it is new evidence"


def test_a_restatement_clears_the_flag(client, world):
    now = datetime.now(UTC)
    client.post("/v1/customers", json={"external_id": "globex", "name": "Globex"}, headers=h(world))
    send(client, world, "preferences_updated", {"message": "We prefer email."}, at=now - timedelta(days=40), customer="globex")
    for day in range(5):
        send(client, world, "chat_message", {"message": "Quick question"}, at=now - timedelta(days=10 - day), customer="globex")
    opened = client.get("/v1/drift", params={"customer_id": "globex"}, headers=h(world)).json()["data"]
    assert [flag["kind"] for flag in opened] == ["channel"] and opened[0]["observed"] == "chat"

    restated = send(client, world, "preferences_updated", {"message": "We prefer email."}, at=now, customer="globex")
    assert restated["drift"]["cleared"] == [opened[0]["id"]]
    after = client.get("/v1/drift", params={"customer_id": "globex", "status": "cleared"}, headers=h(world)).json()["data"]
    assert after[0]["note"] == "The evidence no longer points the other way."
    assert "cleared" in {item["drift"]["status"] for item in deliveries(client, world, "memory.drift_resolved")}


def test_the_dashboard_and_the_quality_report(client, world):
    everything = client.get(f"{world['base']}/drift", params={"status": "all"}, headers=world["auth"]).json()
    # Three confirmed, two dismissed (the habit, and the problem a person vouched for), one cleared.
    assert sorted(flag["status"] for flag in everything["data"]) == [
        "cleared", "confirmed", "confirmed", "confirmed", "dismissed", "dismissed",
    ]
    assert everything == client.get("/v1/drift", params={"status": "all"}, headers=h(world)).json()
    report = client.get(f"{world['base']}/customers/acme/freshness", headers=world["auth"]).json()
    assert report["customer_id"] == "acme" and report["counts"]["outdated"] == 0
    quality = client.get(f"{world['base']}/quality", headers=world["auth"]).json()
    memories = quality["metrics"]["memories"]
    assert set(memories["freshness"]) == {"active", "aging", "stale", "outdated", "conflicted"}
    assert memories["drift_open"] == 0
    assert memories["drift_resolved"] == {"confirmed": 3, "dismissed": 2, "cleared": 1}
    component = next(item for item in quality["components"] if item["key"] == "freshness")
    assert component["detail"] == "Share of standing memories within their type's freshness window."

    confirmed = next(flag for flag in everything["data"] if flag["status"] == "confirmed")
    again = client.post(f"{world['base']}/drift/{confirmed['id']}/dismiss", json={}, headers=world["auth"])
    assert again.status_code == 409 and "already confirmed" in again.text


def test_windows_follow_the_settings(client, world):
    client.put(f"{world['base']}/settings", json={"settings": {"freshness_days": {"preference": 20}}}, headers=world["auth"])
    report = client.get("/v1/customers/acme/freshness", headers=h(world)).json()
    assert report["windows"]["preference"] == 20
    refused = client.put(f"{world['base']}/settings", json={"settings": {"drift_min_share": 0.1}}, headers=world["auth"])
    assert refused.status_code == 422


def test_bad_requests(client, world):
    assert client.get("/v1/drift", params={"kind": "vibes"}, headers=h(world)).status_code == 422
    assert client.get("/v1/drift", params={"status": "maybe"}, headers=h(world)).status_code == 422
    assert client.post("/v1/drift/drf_nothing/confirm", json={}, headers=h(world)).status_code == 404
    reader = client.post(
        f"{world['base']}/api-keys", json={"name": "reader", "scopes": ["memory:read", "customers:read"]}, headers=world["auth"]
    ).json()["api_key"]
    any_flag = flags(client, world, status="all")["data"][0]["id"]
    assert client.post(f"/v1/drift/{any_flag}/dismiss", json={}, headers={"X-API-Key": reader}).status_code == 403
    assert client.get(f"/v1/drift/{any_flag}", headers={"X-API-Key": reader}).status_code == 200


def test_a_memory_past_its_expiry_is_expired_before_the_sweep(client, world, engine):
    """The nightly retention job marks expired memories; until it runs, reads must already
    treat them as expired — or an intent that lapsed on Monday is quoted on Wednesday."""
    from sqlalchemy import text

    client.post("/v1/customers", json={"external_id": "initech", "name": "Initech"}, headers=h(world))
    made = client.post(
        "/v1/memories",
        json={"customer_id": "initech", "type": "intent", "content": "The customer may cancel next quarter."},
        headers=h(world),
    ).json()
    before = client.get("/v1/customers/initech/facts", headers=h(world)).json()["values"]
    assert "cancellation" in before["intents.kinds"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE memories SET expires_at = now() - interval '1 day' WHERE id = :m"), {"m": made["id"]})

    active = client.get("/v1/customers/initech/memories", headers=h(world)).json()
    assert made["id"] not in {item["id"] for item in active["data"]}
    expired = client.get("/v1/customers/initech/memories", params={"status": "expired"}, headers=h(world)).json()
    assert [item["id"] for item in expired["data"]] == [made["id"]]
    assert expired["data"][0]["freshness"]["state"] == "expired"
    after = client.get("/v1/customers/initech/facts", headers=h(world)).json()["values"]
    assert "cancellation" not in after["intents.kinds"]
    brief = client.get("/v1/customers/initech/brief", headers=h(world)).json()
    assert "said they may cancel" not in brief["headline"]
