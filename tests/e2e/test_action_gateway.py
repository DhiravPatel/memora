"""The approval gateway over HTTP (§26 4.5): one call before acting, limits, history, opt-outs.

An action's life — requested, allowed or waiting or denied, then done — and the customer's
action history it leaves behind, which is what "a second credit this month needs a
person" reads. The customer's preferences go through the real pipeline, so the opt-out is
the one the extractor and fact builder found in what they said.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required, run_worker

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]

SCOPES = ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]


@pytest.fixture(scope="module")
def client():
    engine = create_engine(get_settings().sync_database_url)
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
def world(client: TestClient) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={"email": f"gateway-{unique}@example.com", "password": "a-strong-password", "organization_name": f"G {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Gateway"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    saved = client.put(
        f"{base}/settings",
        json={
            "settings": {
                "restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}],
                "guardrails": {
                    "auto_approve": [
                        {"actions": ["process_refund"], "up_to": 50, "max_per_30_days": 2},
                        {"actions": ["issue_credit"], "up_to": 100},
                    ],
                    "rules": [
                        {
                            "name": "second_credit",
                            "actions": ["issue_credit"],
                            "when": "actions.issue_credit.count_30d >= 1",
                            "decision": "require_approval",
                            "message": "A second credit this month needs a person.",
                        }
                    ],
                },
            }
        },
        headers=auth,
    )
    assert saved.status_code == 200, saved.text

    def key(name: str, scopes: list[str]) -> str:
        created = client.post(f"{base}/api-keys", json={"name": name, "scopes": scopes}, headers=auth)
        assert created.status_code == 201, created.text
        return created.json()["api_key"]

    world = {
        "auth": auth,
        "base": base,
        "agent": key("agent", SCOPES),
        "reviewer": key("reviewer", ["memory:read", "approvals:decide"]),
    }
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    for event_type, data in (
        ("preferences_updated", {"message": "Please don't call us, email works best."}),
        ("support_message", {"message": "The payroll export fails every night."}),
        ("support_message", {"message": "The Stripe payout of their salary failed."}),
    ):
        sent = client.post("/v1/events", json={"customer_id": "acme", "event_type": event_type, "data": data}, headers=h(world))
        assert run_worker(sent.json()["event_id"])["status"] == "processed"
    return world


def h(world: dict, key: str = "agent") -> dict:
    return {"X-API-Key": world[key]}


def request_action(client, world, action: str, request: dict | None = None, **extra) -> dict:
    response = client.post(
        "/v1/agent/actions/request",
        json={"customer_id": "acme", "action": action, "request": request or {}, **extra},
        headers=h(world),
    )
    assert response.status_code == 201, response.text
    return response.json()


def complete(client, world, action_id: str, outcome: str = "done", **extra):
    return client.post(f"/v1/agent/actions/{action_id}/complete", json={"outcome": outcome, **extra}, headers=h(world))


def decide(client, world, approval_id: str, verdict: str) -> None:
    response = client.post(
        f"/v1/agent/approvals/{approval_id}/decision", json={"decision": verdict, "note": "checked"}, headers=h(world, "reviewer")
    )
    assert response.status_code == 200, response.text


# ----------------------------------------------------------------------- tests


def test_a_customer_who_asked_not_to_be_called_is_not_called(client, world):
    call = request_action(client, world, "call_customer")
    assert call["status"] == "denied" and call["decision"] == "deny"
    reason = next(item for item in call["reasons"] if item["rule"] == "respect_opt_out")
    assert reason["explanation"] == "The customer asked not to be called."
    assert reason["evidence"], "the memory where they said it"
    assert call["next_step"] == "Do not take this action."
    assert request_action(client, world, "send_email", {"reply": True})["status"] == "allowed"


def test_refunds_under_the_limit_need_nobody_until_the_monthly_cap(client, world):
    first = request_action(client, world, "process_refund", {"amount": 25})
    assert first["status"] == "allowed"
    assert first["summary"].startswith("Approved automatically: a refund of 25 within the limit of 50 (1 of 2")
    assert complete(client, world, first["id"], external_ref="re_1").json()["status"] == "done"

    second = request_action(client, world, "process_refund", {"amount": 30})
    assert second["status"] == "allowed" and "(2 of 2 this month)" in second["summary"]
    complete(client, world, second["id"])

    facts = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]
    assert facts["actions.process_refund.count_30d"] == 2
    assert facts["actions.money.amount_30d"] == 55.0

    third = request_action(client, world, "process_refund", {"amount": 10})
    assert third["status"] == "pending_approval"
    assert "monthly cap of 2" in third["summary"]
    assert third["next_step"].startswith("Wait for a person")
    waiting = client.post(f"/v1/agent/actions/{third['id']}/proceed", headers=h(world)).json()
    assert waiting["status"] == "pending_approval", "nothing is decided yet"

    decide(client, world, third["approval"]["id"], "approve")
    assert client.get(f"/v1/agent/actions/{third['id']}", headers=h(world)).json()["next_step"].startswith(
        "A person approved it"
    )
    went = client.post(f"/v1/agent/actions/{third['id']}/proceed", headers=h(world)).json()
    assert went["status"] == "allowed" and went["approval"]["status"] == "used"
    again = client.post(f"/v1/agent/actions/{third['id']}/proceed", headers=h(world)).json()
    assert again["status"] == "allowed", "proceeding twice changes nothing"


def test_over_the_amount_a_person_decides_and_can_say_no(client, world):
    big = request_action(client, world, "process_refund", {"amount": 80})
    assert big["status"] == "pending_approval"
    assert "over the project's automatic limit of 50" in big["summary"]
    decide(client, world, big["approval"]["id"], "reject")
    # A no settles it at once: the agent need not proceed to find out.
    settled = client.get(f"/v1/agent/actions/{big['id']}", headers=h(world)).json()
    assert settled["status"] == "denied" and settled["next_step"] == "Do not take this action."
    assert settled["summary"] == "A person rejected this request: checked"
    refused = client.post(f"/v1/agent/actions/{big['id']}/proceed", headers=h(world)).json()
    assert refused["status"] == "denied"
    assert complete(client, world, big["id"]).status_code == 409


def test_history_is_a_fact_rules_can_read(client, world):
    first = request_action(client, world, "issue_credit", {"amount": 20})
    assert first["status"] == "allowed"
    complete(client, world, first["id"])
    second = request_action(client, world, "issue_credit", {"amount": 20})
    assert second["status"] == "pending_approval"
    assert second["summary"] == "A second credit this month needs a person."
    evaluated = client.post(
        "/v1/conditions/evaluate",
        json={"customer_id": "acme", "condition": "actions.money.count_30d >= 3 and actions.issue_credit.count_30d == 1"},
        headers=h(world),
    ).json()
    assert evaluated["evaluation"]["matched"] is True
    # A waiting action can be abandoned.
    assert complete(client, world, second["id"], "cancelled").json()["status"] == "cancelled"


def test_the_same_idempotency_key_is_the_same_action(client, world):
    first = request_action(client, world, "send_email", {"reply": True}, idempotency_key="msg-123")
    again = request_action(client, world, "send_email", {"reply": True}, idempotency_key="msg-123")
    assert again["id"] == first["id"]
    clash = client.post(
        "/v1/agent/actions/request",
        json={"customer_id": "acme", "action": "send_email", "request": {"reply": False}, "idempotency_key": "msg-123"},
        headers=h(world),
    )
    assert clash.status_code == 409
    done = complete(client, world, first["id"])
    assert done.status_code == 200 and complete(client, world, first["id"]).json()["status"] == "done"


def test_a_reviewer_reads_the_evidence_in_its_own_words(client, world):
    closing = request_action(client, world, "close_ticket")
    assert closing["status"] == "pending_approval"
    approval_id = closing["approval"]["id"]
    cleared = client.get(f"{world['base']}/agent/approvals/{approval_id}", headers=world["auth"]).json()
    words = [memory["content"] for memory in cleared["evidence_memories"]]
    assert "The payroll export fails every night." in words
    assert cleared["customer"]["name"] == "Acme" and cleared["customer"]["open_problems"] >= 1
    assert cleared["action_id"] == closing["id"]

    # The reviewer key has no clearance: the restricted problem's words stay withheld.
    reviewer = client.get(f"/v1/agent/approvals/{approval_id}", headers=h(world, "reviewer")).json()
    assert reviewer["withheld_evidence"] >= 1
    assert "salary" not in json.dumps(reviewer).lower()


def test_completion_is_announced_and_listed(client, world):
    client.post(
        f"{world['base']}/webhooks",
        json={"url": "https://hooks.example.com/actions", "event_types": ["agent.action_completed"]},
        headers=world["auth"],
    )
    action = request_action(client, world, "create_ticket", {"topic": "export"})
    complete(client, world, action["id"], note="JIRA-42 opened", external_ref="JIRA-42")
    deliveries = client.get(f"{world['base']}/webhooks/deliveries", headers=world["auth"]).json()["data"]
    sent = [item["payload"]["data"] for item in deliveries if item["event_type"] == "agent.action_completed"]
    assert sent and sent[0]["external_ref"] == "JIRA-42" and sent[0]["status"] == "done"

    listed = client.get("/v1/agent/actions", params={"customer_id": "acme", "status": "done"}, headers=h(world)).json()
    assert {item["action"] for item in listed["data"]} >= {"process_refund", "create_ticket"}
    everything = client.get(f"{world['base']}/agent/actions", params={"customer_id": "acme"}, headers=world["auth"])
    assert everything.status_code == 200
    statuses = {item["status"] for item in everything.json()["data"]}
    assert {"done", "denied", "cancelled"} <= statuses
    assert everything.json()["total"] > listed["total"]


def test_bad_requests_are_refused(client, world):
    assert client.post(
        "/v1/agent/actions/request", json={"customer_id": "nobody", "action": "send_email"}, headers=h(world)
    ).status_code == 404
    assert client.post(
        "/v1/agent/actions/request", json={"customer_id": "acme", "action": "send_email", "dry_run": True}, headers=h(world)
    ).status_code == 422
    assert client.get("/v1/agent/actions/act_nope", headers=h(world)).status_code == 404
    assert client.post("/v1/agent/actions/act_nope/complete", json={"outcome": "maybe"}, headers=h(world)).status_code == 422
    broken = client.put(
        f"{world['base']}/settings",
        json={"settings": {"guardrails": {"auto_approve": [{"actions": ["offer_upgrade"], "up_to": 5}]}}},
        headers=world["auth"],
    )
    assert broken.status_code == 422 and "never needs approval" in broken.text


def test_history_follows_the_customer_through_a_merge(client, world):
    client.post("/v1/customers", json={"external_id": "acme-duplicate", "name": "Acme (dup)"}, headers=h(world))
    moved = client.post(
        "/v1/agent/actions/request",
        json={"customer_id": "acme-duplicate", "action": "schedule_call", "request": {"channel": "zoom"}},
        headers=h(world),
    ).json()
    complete(client, world, moved["id"])
    before = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"].get("actions.schedule_call.count_30d", 0)

    merged = client.post(
        "/v1/customers/acme-duplicate/merge", json={"into": "acme"}, headers=h(world)
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["agent_actions_moved"] == 1
    after = client.get("/v1/customers/acme/facts", headers=h(world)).json()["values"]["actions.schedule_call.count_30d"]
    assert after == before + 1
