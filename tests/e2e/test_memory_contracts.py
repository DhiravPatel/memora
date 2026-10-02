"""Memory contracts over HTTP, through the real pipeline (§26 7.1).

A payments team declares what a payment_failed event must look like — in YAML, as the spec
writes it — and then sends events that keep it and events that break it: an amount sent as
"₹500", a missing currency. Warn keeps them and reports how; enforce refuses them and counts
the refusals; the contract's text field and importance reach extraction.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import database_required, run_job, run_worker

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]

CONTRACT = """
event: payment_failed
description: A card or bank payment did not go through.
required: [customer_id, amount, currency]
fields:
  amount: {type: number, minimum: 0}
  currency: {type: string, enum: [USD, EUR, INR]}
  details.reason: {type: string, max_length: 500}
text_field: details.reason
importance: 0.95
"""


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
        json={"email": f"contracts-{unique}@example.com", "password": "a-strong-password", "organization_name": f"C {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Contracts"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    world = {"auth": auth, "base": base, "project_id": project["id"], "admin": project["api_key"]}
    for name, scopes in (
        ("ingest", ["events:write"]),
        ("reader", ["events:write", "memory:read", "customers:read"]),
    ):
        world[name] = client.post(f"{base}/api-keys", json={"name": name, "scopes": scopes}, headers=auth).json()["api_key"]
    client.post(
        f"{base}/webhooks",
        json={"url": "https://hooks.example.com/contracts", "event_types": ["event.contract_violated"]},
        headers=auth,
    )
    return world


def h(world: dict, key: str = "admin") -> dict:
    return {"X-API-Key": world[key]}


def send(client, world, data: dict, *, customer: str = "acme", event_type: str = "payment_failed", key: str = "ingest"):
    return client.post(
        "/v1/events", json={"customer_id": customer, "event_type": event_type, "data": data}, headers=h(world, key)
    )


GOOD = {"amount": 499, "currency": "INR", "details": {"reason": "The card was declined by the bank twice this week."}}


# ----------------------------------------------------------------------- tests


def test_a_contract_written_in_yaml_is_compiled_and_versioned(client, world):
    refused = client.post("/v1/contracts", json={"yaml": CONTRACT}, headers=h(world, "ingest"))
    assert refused.status_code == 403, "saving a contract needs admin"

    created = client.post("/v1/contracts", json={"yaml": CONTRACT}, headers=h(world))
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["event_type"], body["mode"], body["version"]) == ("payment_failed", "warn", 1)
    definition = body["definition"]
    assert definition["required"] == ["amount", "currency"], "customer_id is the envelope's, always required"
    assert definition["fields"]["currency"] == {"type": "string", "required": True, "enum": ["USD", "EUR", "INR"]}
    assert (definition["text_field"], definition["importance"]) == ("details.reason", 0.95)

    same = client.post("/v1/contracts", json={"yaml": CONTRACT}, headers=h(world))
    assert same.status_code == 200 and same.json()["version"] == 1, "an identical save changes nothing"

    bad = client.post(
        "/v1/contracts", json={"event_type": "payment_failed", "fields": {"amount": {"type": "money"}}}, headers=h(world)
    )
    assert bad.status_code == 422 and "type is one of" in bad.json()["error"]["message"]
    unparsable = client.post("/v1/contracts", json={"yaml": "event: [unclosed"}, headers=h(world))
    assert unparsable.status_code == 422 and "YAML" in unparsable.json()["error"]["message"]


def test_warn_keeps_the_event_and_says_how_it_broke_the_contract(client, world, engine):
    sent = send(client, world, {"amount": "₹500", "details": {"reason": "Declined."}})
    assert sent.status_code == 202, sent.text
    check = sent.json()["contract"]
    assert check["valid"] is False and check["mode"] == "warn" and check["version"] == 1
    assert [(item["path"], item["rule"]) for item in check["violations"]] == [("amount", "type"), ("currency", "required")]
    assert check["violations"][0]["received"] == 'string ("₹500")'
    with engine.begin() as connection:
        stored = connection.execute(
            text("SELECT contract->>'valid' FROM events WHERE id = :id"), {"id": sent.json()["event_id"]}
        ).scalar_one()
    assert stored == "false", "kept, with its check"
    read = client.get(f"/v1/events/{sent.json()['event_id']}", headers=h(world, "reader")).json()
    assert read["contract"]["valid"] is False and read["contract"]["version"] == 1

    report = client.get("/v1/contracts/payment_failed", headers=h(world, "ingest")).json()["report"]
    assert (report["events"], report["checked"], report["violating"]) == (1, 1, 1)
    amount = next(item for item in report["violations"] if item["path"] == "amount")
    assert (amount["rule"], amount["expected"], amount["received"], amount["events"]) == ("type", "number", 'string ("₹500")', 1)
    assert report["recent"][0]["event_id"] == sent.json()["event_id"]
    assert (report["recent"][0]["customer_id"], report["recent"][0]["version"]) == ("acme", 1), "as it was sent"


def test_the_text_field_and_importance_reach_the_pipeline(client, world):
    sent = send(client, world, GOOD, customer="globex")
    assert sent.status_code == 202, sent.text
    assert sent.json()["contract"]["valid"] is True
    assert sent.json()["importance"] == 0.95, "the contract's importance"
    outcome = run_worker(sent.json()["event_id"])
    assert outcome["status"] == "processed", outcome
    memories = client.get("/v1/customers/globex/memories", headers=h(world, "reader")).json()["data"]
    assert any("declined" in memory["content"].lower() for memory in memories), (
        "details.reason was read as the text, though it is nested"
    )


def test_enforce_refuses_a_broken_event_and_counts_it(client, world, engine):
    contract = client.get("/v1/contracts/payment_failed", headers=h(world)).json()["definition"]
    updated = client.put("/v1/contracts/payment_failed", json={**contract, "mode": "enforce"}, headers=h(world))
    assert updated.status_code == 200 and updated.json()["version"] == 2

    refused = send(client, world, {"amount": -5, "currency": "GBP"}, customer="initech")
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "contract_violation" and error["details"]["contract_version"] == 2
    assert {(item["path"], item["rule"]) for item in error["details"]["violations"]} == {("amount", "minimum"), ("currency", "enum")}
    assert error["message"].startswith("This payment_failed event breaks its contract: 'amount' is below 0.")
    with engine.begin() as connection:
        customers = connection.execute(
            text("SELECT count(*) FROM customers WHERE project_id = :p AND external_id = 'initech'"), {"p": world["project_id"]}
        ).scalar_one()
    assert customers == 0, "a refused event creates nothing, not even its customer"

    rejected = client.get("/v1/contracts/payment_failed", headers=h(world)).json()["rejected"]
    assert rejected["count"] == 1 and rejected["recent"][0]["customer_id"] == "initech"
    assert send(client, world, GOOD).status_code == 202


def test_a_batch_reports_what_it_refused_and_keeps_the_rest(client, world):
    response = client.post(
        "/v1/events/batch",
        json={
            "events": [
                {"customer_id": "acme", "event_type": "payment_failed", "data": GOOD},
                {"customer_id": "acme", "event_type": "payment_failed", "data": {"currency": "USD"}, "external_event_id": "bad-1"},
                {"customer_id": "acme", "event_type": "support_message", "data": {"message": "Hello there, any news?"}},
            ]
        },
        headers=h(world, "ingest"),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert len(body["accepted"]) == 2
    assert [(item["index"], item["external_event_id"]) for item in body["rejected"]] == [(1, "bad-1")]
    assert body["rejected"][0]["violations"][0]["path"] == "amount"


def test_an_agents_turns_are_never_refused(client, world):
    client.post(
        "/v1/contracts",
        json={"event_type": "agent_conversation", "mode": "enforce", "required": ["ticket_id"]},
        headers=h(world),
    )
    opened = client.post("/v1/agent/sessions", json={"customer_id": "acme", "agent": "support-bot"}, headers=h(world))
    assert opened.status_code == 201, opened.text
    turn = client.post(
        f"/v1/agent/sessions/{opened.json()['id']}/turns",
        json={"role": "user", "content": "Is there any news on the declined card?", "retrieve": False},
        headers=h(world),
    )
    assert turn.status_code == 201, turn.text
    client.delete("/v1/contracts/agent_conversation", headers=h(world))


def test_a_preview_says_what_the_contract_would_do(client, world):
    preview = client.post(
        "/v1/events/preview",
        json={"customer_id": "acme", "event_type": "payment_failed", "data": {"amount": "500", "currency": "USD"}},
        headers=h(world, "reader"),
    )
    assert preview.status_code == 200, preview.text
    contract = preview.json()["contract"]
    assert contract["would_refuse"] is True and contract["violations"][0]["path"] == "amount"


def test_payloads_can_be_tested_and_contracts_drafted_from_traffic(client, world):
    tested = client.post("/v1/contracts/payment_failed/test", json={"data": GOOD}, headers=h(world, "ingest")).json()
    assert tested["valid"] is True and tested["version"] == 2
    assert tested["text"] == 'string ("The card was declined by the bank twice…")'
    proposed = client.post(
        "/v1/contracts/payment_failed/test",
        json={"data": GOOD, "yaml": "event: payment_failed\nfields:\n  amount: {type: string}\n"},
        headers=h(world, "ingest"),
    ).json()
    assert proposed["valid"] is False and proposed["version"] is None
    assert proposed["definition"]["fields"]["amount"] == {"type": "string"}, "how the server read the YAML"

    draft = client.post("/v1/contracts/payment_failed/draft", headers=h(world, "ingest"))
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["fields"]["details.reason"]["type"] == "string" and body["text_field"] == "details.reason"
    assert body["samples"] >= 3
    missing = client.post("/v1/contracts/never_sent/draft", headers=h(world, "ingest"))
    assert missing.status_code == 404


def test_coverage_shows_what_has_no_contract(client, world):
    coverage = {item["event_type"]: item for item in client.get("/v1/contracts/coverage", headers=h(world, "ingest")).json()}
    assert coverage["payment_failed"]["mode"] == "enforce" and coverage["payment_failed"]["violating"] >= 1
    assert coverage["support_message"]["mode"] is None, "received, but no contract covers it"


def test_the_hourly_digest(client, world):
    from worker.tasks.contracts import report_contract_violations

    first = run_job(report_contract_violations)
    assert first["reported"] == 1
    rows = client.get(f"{world['base']}/webhooks/deliveries", params={"limit": 50}, headers=world["auth"]).json()["data"]
    digests = [row["payload"]["data"] for row in rows if row["event_type"] == "event.contract_violated"]
    assert len(digests) == 1
    digest = digests[0]
    assert digest["event_type"] == "payment_failed" and digest["mode"] == "enforce"
    assert digest["violating_events"] == 1 and digest["refused_events"] == 2
    assert {"path": "amount", "rule": "type", "expected": "number", "received": 'string ("₹500")', "events": 1} in digest["violations"]
    assert run_job(report_contract_violations)["reported"] == 0, "nothing new since"


def test_forgetting_a_customer_forgets_their_refusals(client, world):
    assert send(client, world, GOOD, customer="hooli").status_code == 202
    assert send(client, world, {"amount": "lots"}, customer="hooli").status_code == 422
    before = client.get("/v1/contracts/payment_failed", headers=h(world)).json()["rejected"]
    assert "hooli" in [item["customer_id"] for item in before["recent"]]

    deleted = client.delete("/v1/customers/hooli", headers=h(world))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["removed"]["refused_samples"] == 1
    after = client.get("/v1/contracts/payment_failed", headers=h(world)).json()["rejected"]
    assert "hooli" not in [item["customer_id"] for item in after["recent"]]
    assert after["count"] == before["count"], "the count is a number, not the customer"


def test_the_dashboard_and_deleting_a_contract(client, world, engine):
    listed = client.get(f"{world['base']}/contracts", headers=world["auth"]).json()
    assert [item["event_type"] for item in listed] == ["payment_failed"]
    assert listed[0]["events"] >= 3
    deleted = client.delete("/v1/contracts/payment_failed", headers=h(world))
    assert deleted.status_code == 204
    sent = send(client, world, {"amount": "nope"})
    assert sent.status_code == 202 and sent.json()["contract"] is None
    with engine.begin() as connection:
        stored = connection.execute(text("SELECT contract FROM events WHERE id = :id"), {"id": sent.json()["event_id"]}).scalar_one()
    assert stored is None, "no contract, no check — a SQL null"
