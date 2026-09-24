"""Memory evaluation over HTTP (§26 4.3): extraction cases, regressions, the scorecard.

Extraction cases go through the real pipeline as a dry run, so what they measure is what
the extractor, the consolidation planner and the restriction policy would really do —
including the resolution rule and the classifier fixes of §30.5.
"""

from __future__ import annotations

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
        json={"email": f"evals-{unique}@example.com", "password": "a-strong-password", "organization_name": f"E {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Evals"}, headers=auth).json()
    base = f"/v1/projects/{project['id']}"
    client.put(
        f"{base}/settings",
        json={"settings": {"restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}]}},
        headers=auth,
    )
    key = client.post(f"{base}/api-keys", json={"name": "k", "scopes": [*SCOPES, "memory:restricted"]}, headers=auth).json()["api_key"]
    world = {"auth": auth, "base": base, "key": key}
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    # Something already known, so a resolution has a problem to close.
    sent = client.post(
        "/v1/events",
        json={"customer_id": "acme", "event_type": "support_message", "data": {"message": "The payroll export fails every night."}},
        headers=h(world),
    )
    assert run_worker(sent.json()["event_id"])["status"] == "processed"
    world["set"] = client.post("/v1/evals", json={"name": "What we remember"}, headers=h(world)).json()["id"]
    return world


def h(world: dict) -> dict:
    return {"X-API-Key": world["key"]}


def extraction(event_type: str, message: str, **expectations) -> dict:
    return {"customer_id": "acme", "kind": "extraction", "event": {"event_type": event_type, "data": {"message": message}}, **expectations}


CASES = [
    extraction(
        "support_message",
        "The Shopify sync fails during checkout.",
        question="Shopify sync failure",
        expect=[{"type": "problem", "contains": "shopify sync", "entity": "Shopify"}],
    ),
    extraction(
        "support_message",
        "The payroll export works again, thanks for fixing it.",
        question="Payroll fixed",
        expect=[{"contains": "payroll export", "action": "conflict"}],
        forbid=[{"type": "problem", "action": "create"}],
    ),
    extraction(
        "support_message",
        "The Stripe payout of their salary failed.",
        question="Salary payout",
        expect=[{"contains": "salary", "sensitivity": "restricted"}],
    ),
    extraction(
        "support_message",
        "Weekly campaigns to all 50k subscribers are now live, it went out this morning.",
        question="Good news is not a problem",
        forbid=[{"type": "problem"}],
    ),
    {"customer_id": "acme", "kind": "extraction", "question": "Page views are noise", "event": {"event_type": "page_view", "data": {"path": "/dashboard"}}, "expect_nothing": True},
    # Deliberately wrong, to see what a failure explains.
    extraction(
        "support_message",
        "The export is slow and keeps timing out.",
        question="Mistyped on purpose",
        expect=[{"type": "goal", "contains": "export slow"}],
    ),
    {"customer_id": "acme", "question": "What fails every night?", "expected_phrases": ["payroll export"]},
]


def test_extraction_cases_are_validated(client, world):
    for bad, message in (
        ({"customer_id": "acme", "kind": "extraction", "expect": [{"type": "problem"}]}, "needs an event"),
        (extraction("support_message", "x", expect=[{"colour": "red"}]), "colour"),
        (extraction("support_message", "x", expect=[{"type": "worry"}]), "not a memory type"),
        (extraction("support_message", "x", expect=[{"contains": "x"}], expect_nothing=True), "expect nothing at once"),
        (extraction("support_message", "x"), "say what the event should become"),
        (extraction("support_message", "x", expect=[{"action": "delete"}]), "action is one of"),
    ):
        response = client.post(f"/v1/evals/{world['set']}/cases", json={"cases": [bad]}, headers=h(world))
        assert response.status_code == 422, (bad, response.text)
        assert message in response.text


def test_a_mixed_set_measures_retrieval_and_extraction(client, world):
    added = client.post(f"/v1/evals/{world['set']}/cases", json={"cases": CASES}, headers=h(world))
    assert added.status_code == 201, added.text
    kinds = [case["kind"] for case in added.json()]
    assert kinds.count("extraction") == 6 and kinds.count("retrieval") == 1

    run = client.post(f"/v1/evals/{world['set']}/runs", json={"label": "baseline"}, headers=h(world)).json()
    assert run["status"] == "succeeded", run
    extraction_metrics = run["metrics"]["extraction"]
    assert extraction_metrics["cases"] == 6
    assert extraction_metrics["passed"] == 5, run["results"]
    assert extraction_metrics["false_memory_rate"] == 0.0
    assert run["metrics"]["recall"]["@5"] == 1.0  # the retrieval question still scores as before

    by_label = {result.get("label") or result.get("question"): result for result in run["results"]}
    wrong = by_label["Mistyped on purpose"]
    assert wrong["passed"] is False
    assert wrong["expected"][0]["near_miss"] == "typed problem, expected goal"
    assert by_label["Payroll fixed"]["passed"] is True, "the resolution supersedes the problem"
    assert by_label["Page views are noise"]["stop_reason"]
    assert "closest_content" not in by_label["Payroll fixed"]["planned"][0], "an existing memory's words are not stored"


def test_a_regression_names_what_a_proposed_setting_would_break(client, world):
    before = client.get(f"{world['base']}/settings", headers=world["auth"]).json()
    response = client.post(
        f"/v1/evals/{world['set']}/regression", json={"settings": {"min_event_importance": 0.99}}, headers=h(world)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["safe"] is False
    broken = {item["label"] for item in body["newly_failing"]}
    assert {"Shopify sync failure", "Salary payout"} <= broken
    assert body["summary"].startswith(f"{len(body['newly_failing'])} previously passing case")
    assert body["proposed"]["overrides"] == {"min_event_importance": 0.99} and body["proposed"]["proposed"] is True
    assert body["current"]["overrides"] is None and body["current"]["proposed"] is False
    # Proposed, never saved.
    after = client.get(f"{world['base']}/settings", headers=world["auth"]).json()
    assert after["values"]["min_event_importance"] == before["values"]["min_event_importance"] != 0.99

    # A run under proposed settings is never the next run's baseline.
    next_run = client.post(f"/v1/evals/{world['set']}/runs", json={}, headers=h(world)).json()
    assert next_run["baseline_run_id"] == body["current"]["id"]

    harmless = client.post(
        f"/v1/evals/{world['set']}/regression", json={"settings": {"decay_days": 120}}, headers=h(world)
    ).json()
    assert harmless["safe"] is True and harmless["summary"] == "Nothing would change: every case keeps its result."
    refused = client.post(
        f"/v1/evals/{world['set']}/regression", json={"settings": {"consolidation_similarity": 7}}, headers=h(world)
    )
    assert refused.status_code == 422


def test_the_scorecard_brings_it_together(client, world):
    card = client.get("/v1/evals/scorecard", headers=h(world)).json()
    assert card["extraction"]["cases"] == 6
    assert card["extraction"]["accuracy"] == round(5 / 6, 4)
    assert card["extraction"]["type_accuracy"] is not None
    assert card["retrieval"]["questions"] == 1 and card["retrieval"]["recall_at_5"] == 1.0
    assert set(card["memory"]) >= {"duplicate_control", "consistency", "quality_score"}
    assert card["gaps"] == []
    assert card["sets"][0]["name"] == "What we remember"
    dashboard = client.get(f"{world['base']}/evals/scorecard", headers=world["auth"])
    assert dashboard.status_code == 200 and dashboard.json()["extraction"] == card["extraction"]
