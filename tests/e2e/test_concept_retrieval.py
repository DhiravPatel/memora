"""Concept retrieval over HTTP — and its gain, measured rather than asserted.

The last test is the one §26's sixth invariant asks for: the same evaluation set run with
the concept leg off and then on, and the comparison says whether it helped.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import database_required

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
        json={"email": f"c-{unique}@example.com", "password": "a-strong-password", "organization_name": f"C {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Concepts"}, headers=auth).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "k", "scopes": ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]},
        headers=auth,
    ).json()["api_key"]
    return {"auth": auth, "project_id": project["id"], "key": key}


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


MEMORIES = {
    "connector": ("problem", "The connector has stopped functioning since Tuesday."),
    "charges": ("problem", "The customer was double charged on the latest invoice."),
    "login": ("problem", "Nobody on the team can sign in after the SAML change."),
    "slowness": ("problem", "Exports crawl and take forever to finish."),
    "channel": ("preference", "The customer prefers WhatsApp for support."),
    "seats": ("fact", "The account has forty seats across three offices."),
}

# Asked in words none of the memories use.
PARAPHRASES = [
    ("Is the integration broken?", "connector"),
    ("Any billing mistakes or refunds needed?", "charges"),
    ("Are there authentication problems with SSO?", "login"),
    ("Is anything slow or timing out?", "slowness"),
]


@pytest.fixture(scope="module")
def customer(client: TestClient, account: dict) -> dict:
    client.post("/v1/customers", json={"external_id": "cus_c"}, headers=h(account["key"]))
    ids = {}
    for label, (memory_type, content) in MEMORIES.items():
        created = client.post(
            "/v1/memories",
            json={"customer_id": "cus_c", "content": content, "type": memory_type},
            headers=h(account["key"]),
        )
        assert created.status_code == 201, created.text
        ids[label] = created.json()["id"]
    return ids


def settings(client, account, **values) -> None:
    response = client.put(
        f"/v1/projects/{account['project_id']}/settings", json={"settings": values}, headers=account["auth"]
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------- indexing


def test_a_memory_written_through_the_api_is_embedded_and_conceptualised(client, account, customer):
    """It used to be neither — invisible to the semantic leg, and to this one."""
    engine = create_engine(get_settings().sync_database_url)
    with engine.connect() as connection:
        embedded = connection.execute(
            text("SELECT count(*) FROM embeddings WHERE memory_id = :id"), {"id": customer["connector"]}
        ).scalar()
        concepts = connection.execute(
            text("SELECT concepts FROM memories WHERE id = :id"), {"id": customer["connector"]}
        ).scalar()
    engine.dispose()
    assert embedded == 1
    assert set(concepts) >= {"integration", "broken"}


# ------------------------------------------------------------------ retrieval


def test_a_paraphrase_finds_a_memory_that_shares_no_words(client, account, customer):
    found = client.post(
        "/v1/memory/search",
        json={"customer_id": "cus_c", "query": "Is the integration broken?", "limit": 3},
        headers=h(account["key"]),
    ).json()
    top = found["memories"][0]
    assert top["id"] == customer["connector"]
    assert "concept" in top["retrieved_by"]


def test_the_trace_says_which_concepts_matched(client, account, customer):
    answer = client.post(
        "/v1/memory/query",
        json={"customer_id": "cus_c", "query": "Is the integration broken?", "include_trace": True},
        headers=h(account["key"]),
    ).json()
    ranking = {row["memory_id"]: row for row in answer["trace"]["ranking"]}
    assert set(ranking[customer["connector"]]["matched_concepts"]) == {"broken", "integration"}


def test_the_concept_leg_can_be_switched_off(client, account, customer):
    settings(client, account, concept_retrieval=False)
    found = client.post(
        "/v1/memory/search",
        json={"customer_id": "cus_c", "query": "Is the integration broken?", "limit": 6},
        headers=h(account["key"]),
    ).json()
    assert all("concept" not in memory["retrieved_by"] for memory in found["memories"])
    settings(client, account, concept_retrieval=True)


def test_a_vague_context_request_still_briefs_the_agent(client, account, customer):
    """One loose match must not crowd out the customer's open problems."""
    context = client.post(
        "/v1/memory/context",
        json={"customer_id": "cus_c", "task": "support_response"},
        headers=h(account["key"]),
    ).json()
    problems = context["customer_context"]["active_problems"]
    assert len(problems) >= 3, context["customer_context"]


# ------------------------------------------------------------------ measured


def test_the_gain_is_measured_not_asserted(client, account, customer):
    eval_set = client.post("/v1/evals", json={"name": "Paraphrases"}, headers=h(account["key"])).json()["id"]
    client.post(
        f"/v1/evals/{eval_set}/cases",
        json={
            "cases": [
                {"customer_id": "cus_c", "question": question, "expected_memory_ids": [customer[label]]}
                for question, label in PARAPHRASES
            ]
        },
        headers=h(account["key"]),
    )

    settings(client, account, concept_retrieval=False)
    without = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "concepts off", "k": 3}, headers=h(account["key"])).json()
    settings(client, account, concept_retrieval=True)
    with_concepts = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "concepts on", "k": 3}, headers=h(account["key"])).json()

    assert without["settings"]["concept_retrieval"] is False
    assert with_concepts["settings"]["concept_retrieval"] is True
    off, on = without["metrics"], with_concepts["metrics"]
    assert on["mrr"] > off["mrr"], (off, on)
    assert on["hit"]["@1"] >= 0.75, on
    assert with_concepts["comparison"]["mrr"] > 0
    assert with_concepts["comparison"]["regressed"] is False
