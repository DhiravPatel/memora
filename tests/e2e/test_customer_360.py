"""One call instead of eight.

The property this file exists to defend is **agreement**: a 360 is an aggregation, not a
second opinion. If the health score here differs from the one `/health` returns, the 360 is
wrong — and it is the version an agent will act on, so it is wrong in the expensive
direction. Most of what follows compares the two.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required

from app.main import create_app
from app.services.customer360_service import PER_TYPE, SECTIONS
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
            "email": f"c360-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"C360 {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "C360"}, headers=auth).json()

    applied = client.put(
        f"/v1/projects/{project['id']}/settings",
        json={
            "settings": {
                "restriction_policies": [
                    {"kind": "term", "value": ["salary"], "label": "compensation"}
                ]
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


def headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def customer(client: TestClient, account: dict) -> str:
    """A customer with something in most sections."""
    external_id = "cus_360"
    created = client.post(
        "/v1/customers",
        json={"external_id": external_id, "name": "Three Sixty Ltd"},
        headers=headers(account["key"]),
    )
    assert created.status_code == 201, created.text

    written = [
        ("problem", "The Shopify sync fails during checkout and orders are lost."),
        ("problem", "The CSV import times out on files above 10MB."),
        ("preference", "The customer prefers to be contacted on WhatsApp."),
        ("fact", "The customer runs a team of forty in Berlin."),
        ("subscription", "The customer upgraded from Starter to Pro."),
        ("fact", "The customer asked about their salary reporting module."),
    ]
    for memory_type, content in written:
        response = client.post(
            "/v1/memories",
            json={"customer_id": external_id, "content": content, "type": memory_type},
            headers=headers(account["key"]),
        )
        assert response.status_code == 201, response.text
    return external_id


def three_sixty(client, account, customer, key: str | None = None, **params) -> dict:
    response = client.get(
        f"/v1/customers/{customer}/360",
        params=params or None,
        headers=headers(key or account["key"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------------ shape


def test_one_call_returns_every_section(client, account, customer):
    view = three_sixty(client, account, customer)

    assert set(view["sections"]) == set(SECTIONS)
    assert view["customer"]["external_id"] == customer
    assert view["customer"]["name"] == "Three Sixty Ltd"
    assert view["summary"]
    assert view["generated_at"]


def test_the_sections_carry_what_was_written(client, account, customer):
    sections = three_sixty(client, account, customer)["sections"]

    problems = [item["content"] for item in sections["active_problems"]]
    assert any("Shopify" in content for content in problems)
    assert any("CSV" in content for content in problems)
    assert any("WhatsApp" in item["content"] for item in sections["preferences"])
    assert sections["subscription"] is not None
    assert "Pro" in sections["subscription"]["content"]


def test_a_memory_carries_enough_to_be_trusted(client, account, customer):
    """An agent quoting a memory needs to know how much to believe it."""
    problem = three_sixty(client, account, customer)["sections"]["active_problems"][0]

    assert set(problem) >= {
        "id", "type", "content", "importance", "confidence",
        "evidence_count", "first_seen_at", "last_seen_at", "sensitivity",
    }


def test_the_subscription_is_the_newest_statement_not_the_loudest(client, account):
    """Every other section ranks by importance. A plan is a *state*, so it ranks by time.

    Without the exception, a dramatic old downgrade outranks a quiet recent upgrade and the
    360 reports the wrong plan — to an agent, confidently.
    """
    external_id = "cus_plan"
    client.post(
        "/v1/customers", json={"external_id": external_id}, headers=headers(account["key"])
    )
    for content, importance in (
        ("The customer downgraded from Enterprise to Starter after an outage.", 0.95),
        ("The customer moved onto the Pro plan.", 0.30),
    ):
        response = client.post(
            "/v1/memories",
            json={
                "customer_id": external_id,
                "content": content,
                "type": "subscription",
                "importance": importance,
            },
            headers=headers(account["key"]),
        )
        assert response.status_code == 201, response.text

    subscription = three_sixty(client, account, external_id)["sections"]["subscription"]

    assert "Pro plan" in subscription["content"], "the newest statement is the current one"
    assert subscription["importance"] < 0.95
    # The one it replaced is still reachable, so "what changed?" is answerable.
    assert any("downgraded" in item["content"] for item in subscription["history"])


# ------------------------------------------------- agreement with the originals


def test_health_is_the_same_number_the_health_endpoint_returns(client, account, customer):
    """The whole design: aggregation, not a second opinion."""
    view = three_sixty(client, account, customer)["sections"]["health"]
    direct = client.get(
        f"/v1/customers/{customer}/health", headers=headers(account["key"])
    ).json()

    assert view["score"] == direct["score"]
    assert view["band"] == direct["band"]
    assert view["churn_risk"] == direct["churn_risk"]
    assert [factor["key"] for factor in view["factors"]] == [
        factor["key"] for factor in direct["factors"]
    ]
    assert view["explanation"] == direct["explanation"]


def test_signals_and_actions_are_the_same_as_their_endpoints(client, account, customer):
    sections = three_sixty(client, account, customer)["sections"]
    signals = client.get(
        f"/v1/customers/{customer}/signals", headers=headers(account["key"])
    ).json()
    actions = client.get(
        f"/v1/customers/{customer}/recommendations", headers=headers(account["key"])
    ).json()

    assert sections["risk_signals"]["churn_risk"] == signals["churn_risk"]
    assert sections["risk_signals"]["trajectory"] == signals["trajectory"]
    assert [item["key"] for item in sections["recommended_actions"]] == [
        item["key"] for item in actions["recommendations"]
    ][: len(sections["recommended_actions"])]


# ------------------------------------------------------------------- clearance


def test_a_restricted_memory_does_not_appear_anywhere_in_the_view(client, account, customer):
    """Eleven sections is eleven chances to leak; the count is reported instead."""
    view = three_sixty(client, account, customer, key=account["uncleared"])

    import json as _json

    assert "salary" not in _json.dumps(view).lower()
    assert view["withheld"] >= 1


def test_the_health_score_is_the_same_for_both_readers(client, account, customer):
    """A score is a number about a customer, not a quote from one."""
    cleared = three_sixty(client, account, customer)["sections"]["health"]
    uncleared = three_sixty(client, account, customer, key=account["uncleared"])["sections"][
        "health"
    ]

    assert cleared["score"] == uncleared["score"]


# -------------------------------------------------------------------- filtering


def test_include_builds_only_what_was_asked_for(client, account, customer):
    view = three_sixty(client, account, customer, include="health,active_problems")

    assert set(view["sections"]) == {"health", "active_problems"}


def test_an_unasked_section_is_absent_rather_than_empty(client, account, customer):
    """An agent has to be able to tell "no open problems" from "I did not look"."""
    view = three_sixty(client, account, customer, include="health")

    assert "active_problems" not in view["sections"]
    assert three_sixty(client, account, customer)["sections"]["goals"] == []


def test_the_summary_only_claims_what_was_built(client, account, customer):
    full = three_sixty(client, account, customer)["summary"]
    health_only = three_sixty(client, account, customer, include="health")["summary"]

    assert "open problem" in full
    assert "open problem" not in health_only


def test_an_unknown_section_is_refused(client, account, customer):
    """Silently returning fewer sections gets diagnosed as "it forgot my customer"."""
    response = client.get(
        f"/v1/customers/{customer}/360",
        params={"include": "helth"},
        headers=headers(account["key"]),
    )
    assert response.status_code == 422, response.text
    assert "helth" in response.json()["error"]["message"]


# ----------------------------------------------------------------------- bounds


def test_every_section_is_capped(client, account, customer):
    """The consumer is usually a model with a token budget."""
    sections = three_sixty(client, account, customer)["sections"]

    for name in ("active_problems", "preferences", "important_memories", "goals"):
        assert len(sections[name]) <= PER_TYPE


def test_the_dashboard_sees_the_same_view(client, account, customer):
    by_key = three_sixty(client, account, customer)
    by_admin = client.get(
        f"/v1/projects/{account['project_id']}/customers/{customer}/360",
        headers=account["auth"],
    ).json()

    assert by_admin["sections"]["health"]["score"] == by_key["sections"]["health"]["score"]
    assert set(by_admin["sections"]) == set(by_key["sections"])
