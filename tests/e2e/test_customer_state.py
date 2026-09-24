"""Facts, conditions, lifecycle states and snapshots — over HTTP, against a real database.

Phase 1 of §26 is the substrate the later phases stand on, so these tests pin its
contracts from the outside: a condition means the same thing through every surface, the
lifecycle moves customers only when its rules say so and never while a person has pinned
them, a snapshot is written only when something material changed, and a reader without
clearance cannot learn a restricted memory through a fact, a condition, a trace or a diff.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required, run_worker

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]


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
            "email": f"state-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"State {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "State"}, headers=auth).json()

    applied = client.put(
        f"/v1/projects/{project['id']}/settings",
        json={
            "settings": {
                "restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]
            }
        },
        headers=auth,
    )
    assert applied.status_code == 200, applied.text

    scopes = ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]
    cleared = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "cleared", "scopes": [*scopes, "memory:restricted"]},
        headers=auth,
    )
    uncleared = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "uncleared", "scopes": scopes},
        headers=auth,
    )
    assert cleared.status_code == 201 and uncleared.status_code == 201
    return {
        "auth": auth,
        "project_id": project["id"],
        "key": cleared.json()["api_key"],
        "uncleared": uncleared.json()["api_key"],
    }


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def make_customer(client, account, external_id: str, memories: list[tuple[str, str]]) -> str:
    created = client.post(
        "/v1/customers", json={"external_id": external_id, "name": external_id}, headers=h(account["key"])
    )
    assert created.status_code == 201, created.text
    for memory_type, content in memories:
        remember(client, account, external_id, memory_type, content)
    return external_id


def remember(client, account, external_id: str, memory_type: str, content: str) -> None:
    response = client.post(
        "/v1/memories",
        json={"customer_id": external_id, "content": content, "type": memory_type},
        headers=h(account["key"]),
    )
    assert response.status_code == 201, response.text


def refresh(client, account, external_id: str) -> dict:
    response = client.post(f"/v1/customers/{external_id}/state/refresh", headers=h(account["key"]))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- the language


def test_the_catalog_describes_every_fact(client, account):
    catalog = client.get("/v1/conditions/catalog", headers=h(account["key"])).json()
    names = {fact["name"] for fact in catalog["facts"]}
    assert {"health.score", "problems.entities", "intents.kinds", "state.current"} <= names
    assert catalog["metadata_prefix"] == "customer.metadata."
    assert catalog["examples"]


def test_validate_returns_the_canonical_form(client, account):
    ok = client.post(
        "/v1/conditions/validate",
        json={"condition": "health.score<60 AND problems.entities contains shopify"},
        headers=h(account["key"]),
    ).json()
    assert ok["valid"] is True
    assert ok["text"] == 'health.score < 60 and problems.entities contains "shopify"'
    assert ok["facts"] == ["health.score", "problems.entities"]


def test_validate_points_at_the_mistake(client, account):
    bad = client.post(
        "/v1/conditions/validate", json={"condition": "health.score < 60 and @"}, headers=h(account["key"])
    ).json()
    assert bad["valid"] is False
    assert bad["position"] == len("health.score < 60 and ")


# ------------------------------------------------------------------ facts


def test_facts_reflect_what_was_written(client, account):
    external_id = make_customer(
        client,
        account,
        "cus_facts",
        [
            ("problem", "The Shopify sync fails during checkout."),
            ("preference", "The customer prefers to be contacted on WhatsApp."),
            ("subscription", "The customer upgraded from the Starter plan to the Pro plan."),
        ],
    )
    facts = client.get(f"/v1/customers/{external_id}/facts", headers=h(account["key"])).json()
    values = facts["values"]

    assert values["problems.open_count"] == 1
    assert values["problems.entities"] == ["shopify"]
    assert values["preferences.channel"] == "whatsapp"
    assert values["subscription.plan"] == "pro"
    assert values["subscription.previous_plan"] == "starter"
    assert values["subscription.direction"] == "upgraded"
    assert facts["evidence"]["problems.entities"], "a fact must cite what it came from"


def test_evaluate_explains_and_cites(client, account):
    result = client.post(
        "/v1/conditions/evaluate",
        json={"customer_id": "cus_facts", "condition": 'problems.entities contains "shopify" and subscription.plan == "pro"'},
        headers=h(account["key"]),
    ).json()
    evaluation = result["evaluation"]
    assert evaluation["outcome"] == "true"
    assert evaluation["matched"] is True
    assert evaluation["evidence"], "the memory that mentions Shopify is the evidence"
    assert "problems.entities" in evaluation["explanation"]


def test_evaluate_refuses_an_invalid_condition(client, account):
    response = client.post(
        "/v1/conditions/evaluate",
        json={"customer_id": "cus_facts", "condition": "health.scor < 60"},
        headers=h(account["key"]),
    )
    assert response.status_code == 422, response.text
    assert "health.score" in response.json()["error"]["message"]


# -------------------------------------------------------------- clearance


def test_a_condition_cannot_probe_a_restricted_memory(client, account):
    """Without this, `problems.terms contains "salary"` would be a yes/no oracle."""
    make_customer(
        client,
        account,
        "cus_probe",
        [
            ("problem", "The payroll export has the wrong salary figures."),
            ("problem", "The Shopify sync fails during checkout."),
        ],
    )
    probe = {"customer_id": "cus_probe", "condition": 'problems.terms contains "salary"'}

    cleared = client.post("/v1/conditions/evaluate", json=probe, headers=h(account["key"])).json()
    uncleared = client.post("/v1/conditions/evaluate", json=probe, headers=h(account["uncleared"])).json()

    assert cleared["evaluation"]["matched"] is True
    assert uncleared["evaluation"]["matched"] is False
    assert "problems.terms" in uncleared["withheld_facts"]


def test_redacted_facts_keep_counts_and_drop_content(client, account):
    view = client.get("/v1/customers/cus_probe/facts", headers=h(account["uncleared"])).json()
    values = view["values"]
    assert values["problems.open_count"] == 2, "a count is an aggregate; it stays whole"
    assert "salary" not in values["problems.terms"]
    assert "shopify" in values["problems.terms"]
    assert values["memories.restricted_count"] == 1


# -------------------------------------------------------------- lifecycle


def test_a_new_customer_is_placed_and_moved_by_the_rules(client, account):
    """Cancellation language is an at_risk rule; a fresh customer goes straight there."""
    make_customer(
        client, account, "cus_risk", [("problem", "The customer will cancel if the Shopify sync is not fixed.")]
    )
    moved = refresh(client, account, "cus_risk")
    assert moved["state"] == "at_risk"
    assert [step["transition"] for step in moved["transitions"]] == ["at_risk"]

    state = client.get("/v1/customers/cus_risk/state", headers=h(account["key"])).json()
    assert state["current"]["state"] == "at_risk"
    assert state["current"]["transition"] == "at_risk"
    assert "cancellation" in state["current"]["reason"]
    assert state["current"]["evidence"], "the transition cites the memory that moved it"

    history = client.get("/v1/customers/cus_risk/state/history", headers=h(account["key"])).json()
    assert [row["state"] for row in history["data"]] == ["at_risk", "onboarding"]
    assert history["data"][1]["source"] == "initial"
    assert history["data"][0]["previous_state"] == "onboarding"


def test_a_plan_journey_moves_through_the_states_it_should(client, account):
    make_customer(client, account, "cus_trial", [("subscription", "The customer is on the Trial plan.")])
    assert refresh(client, account, "cus_trial")["state"] == "trial"

    remember(client, account, "cus_trial", "subscription", "The customer upgraded from the Trial plan to the Pro plan.")
    converted = refresh(client, account, "cus_trial")
    assert converted["state"] == "onboarding"
    assert converted["transitions"][0]["transition"] == "converted"


def test_nothing_changes_when_nothing_changed(client, account):
    again = refresh(client, account, "cus_trial")
    assert again["moved"] is False
    assert again["snapshot_id"] is None, "an unchanged state writes no snapshot"


def test_a_pinned_state_is_left_alone_until_released(client, account):
    pinned = client.put(
        "/v1/customers/cus_risk/state",
        json={"state": "active", "note": "Spoke to them — they are fine."},
        headers=h(account["key"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["source"] == "manual"
    assert pinned.json()["pinned"] is True

    # The rules would put them straight back in at_risk; the pin must win.
    assert refresh(client, account, "cus_risk")["state"] == "active"

    released = client.delete("/v1/customers/cus_risk/state/pin", headers=h(account["key"]))
    assert released.status_code == 200, released.text
    assert refresh(client, account, "cus_risk")["state"] == "at_risk"


def test_an_unknown_state_is_refused(client, account):
    response = client.put("/v1/customers/cus_risk/state", json={"state": "happy"}, headers=h(account["key"]))
    assert response.status_code == 422, response.text
    assert "at_risk" in response.json()["error"]["message"]


def test_the_lifecycle_reports_who_is_where(client, account):
    overview = client.get("/v1/lifecycle", headers=h(account["key"])).json()
    assert overview["enabled"] is True
    assert overview["states"][0] == "trial"
    assert overview["counts"]["at_risk"] >= 1

    at_risk = client.get("/v1/lifecycle/customers", params={"state": "at_risk"}, headers=h(account["key"])).json()
    assert "cus_risk" in [customer["external_id"] for customer in at_risk["data"]]


def test_a_broken_machine_is_refused_when_saved(client, account):
    response = client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={"settings": {"lifecycle": {"states": ["a", "b"], "transitions": [{"to": "b", "when": "health.scor < 1"}]}}},
        headers=account["auth"],
    )
    assert response.status_code == 422, response.text
    assert "health.score" in response.json()["error"]["message"]


def test_a_trace_withholds_restricted_values_from_an_uncleared_reader(client, account):
    make_customer(
        client,
        account,
        "cus_trace",
        [("problem", "They will cancel because the salary report is wrong.")],
    )
    refresh(client, account, "cus_trace")

    cleared = client.get("/v1/customers/cus_trace/state", headers=h(account["key"])).json()["current"]
    uncleared = client.get("/v1/customers/cus_trace/state", headers=h(account["uncleared"])).json()["current"]

    assert cleared["state"] == uncleared["state"] == "at_risk"
    leaves = uncleared["evaluation"]["leaves"]
    intents = next(leaf for leaf in leaves if leaf["fact"] == "intents.kinds")
    assert intents["actual"] == "[withheld]"
    # The decision and the rule are still visible — only the value is withheld.
    assert intents["outcome"] == "true"


# --------------------------------------------------------------- snapshots


def test_a_snapshot_records_what_changed(client, account):
    snapshots = client.get("/v1/customers/cus_trial/snapshots", headers=h(account["key"])).json()
    assert snapshots["total"] >= 2
    latest = snapshots["data"][0]
    assert latest["state"] == "onboarding"
    changed = {change["fact"] for change in latest["changes"]}
    assert "subscription.plan" in changed
    plan = next(change for change in latest["changes"] if change["fact"] == "subscription.plan")
    assert (plan["before"], plan["after"]) == ("trial", "pro")


def test_what_we_knew_at_a_moment(client, account):
    now = datetime.now(UTC)
    current = client.get(
        "/v1/customers/cus_trial/snapshots/at", params={"time": now.isoformat()}, headers=h(account["key"])
    ).json()
    assert current["facts"]["subscription.plan"] == "pro"

    before_anything = client.get(
        "/v1/customers/cus_trial/snapshots/at",
        params={"time": (now - timedelta(days=365)).isoformat()},
        headers=h(account["key"]),
    )
    assert before_anything.status_code == 404


def test_a_snapshot_diff_does_not_leak_through_redaction(client, account):
    """The facts are redacted — the changes list must be too, or it says `added: [stripe]`.

    The restricted memory mentions an entity, so the full diff genuinely records it being
    added; the redacted view must show neither the entity nor the word that restricted it.
    """
    import json as _json

    make_customer(
        client,
        account,
        "cus_diff",
        [
            ("problem", "The Shopify sync fails during checkout."),
            ("problem", "The Stripe payout of their salary failed."),
        ],
    )
    refresh(client, account, "cus_diff")

    full = client.get("/v1/customers/cus_diff/snapshots", headers=h(account["key"])).json()
    entities = next(change for change in full["data"][0]["changes"] if change["fact"] == "problems.entities")
    assert "stripe" in entities["after"], "the full diff records the restricted memory's entity"

    hidden = client.get("/v1/customers/cus_diff/snapshots", headers=h(account["uncleared"])).json()
    blob = _json.dumps(hidden).lower()
    assert "stripe" not in blob and "salary" not in blob
    assert "shopify" in blob, "what is not restricted is still reported"

    detail = client.get(
        f"/v1/customers/cus_diff/snapshots/{hidden['data'][0]['id']}", headers=h(account["uncleared"])
    ).json()
    assert "problems.entities" in detail["withheld_facts"]
    assert detail["facts"]["problems.entities"] == ["shopify"]


# ------------------------------------------------------- the worker path


def test_processing_an_event_places_the_customer_and_snapshots_them(client, account):
    """The real process_event task, run inline: the lifecycle is part of the pipeline."""
    make_customer(client, account, "cus_worker", [])
    sent = client.post(
        "/v1/events",
        json={
            "customer_id": "cus_worker",
            "event_type": "support_message",
            "data": {"message": "We are going to cancel unless the Shopify sync is fixed this week."},
        },
        headers=h(account["key"]),
    )
    assert sent.status_code == 202, sent.text
    outcome = run_worker(sent.json()["event_id"])

    assert outcome["status"] == "processed"
    assert outcome["lifecycle_state"] == "at_risk"
    snapshots = client.get("/v1/customers/cus_worker/snapshots", headers=h(account["key"])).json()
    assert snapshots["total"] >= 1
    assert snapshots["data"][0]["reason"] in ("state_change", "event")
