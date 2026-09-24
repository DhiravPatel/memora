"""Keyword recall for natural questions — measured, not asserted (§26 invariant 6).

Web-search syntax requires every word of a query, so a question that says a little more
than the memory it is about ("are the webhook retries *still* failing *today*?") found
nothing by keyword. The same evaluation set is run with keyword search requiring every
word, then matching any meaningful word; the comparison says whether it helped.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base

pytestmark = [pytest.mark.e2e, database_required]

MEMORIES = {
    "webhook": ("problem", "The webhook retries are failing again."),
    "importer": ("problem", "The CSV importer times out on files over 10MB."),
    "invoices": ("problem", "Invoices are sent to the wrong billing contact."),
    "sso": ("problem", "SSO login loops back to the start page."),
    "channel": ("preference", "The customer prefers WhatsApp for support."),
    "seats": ("fact", "The account has forty seats across three offices."),
    "exports": ("problem", "Scheduled exports arrive empty on Mondays."),
    "renewal": ("fact", "Their contract renews in March."),
    # Distractors that share the targets' vocabulary, as a real customer's memories do.
    "d1": ("problem", "The webhook endpoint returns a 500 error for large payloads."),
    "d2": ("fact", "Webhook signing secrets were rotated last month."),
    "d3": ("problem", "Retries for failed card payments were paused by the bank."),
    "d4": ("problem", "The CSV exporter adds an extra blank column."),
    "d5": ("problem", "Importing contacts from HubSpot is slow for large lists."),
    "d6": ("fact", "Invoices are generated on the first of the month."),
    "d7": ("fact", "The billing contact changed to Priya in finance."),
    "d8": ("fact", "SSO is configured with Okta for the whole company."),
    "d9": ("problem", "The start page loads slowly on mobile devices."),
    "d10": ("problem", "Scheduled reports arrive late on Mondays."),
    "d11": ("fact", "Exports are scheduled weekly for the finance team."),
    "d12": ("fact", "The contract includes a renewal discount of ten percent."),
    "d13": ("problem", "The login page shows an error after a password reset."),
    "d14": ("problem", "Retries on the Slack integration stop after three attempts."),
}

# Each says a little more than the memory it is about.
QUESTIONS = [
    ("Are the webhook retries still failing today?", "webhook"),
    ("Did the CSV importer time out again last week?", "importer"),
    ("Which billing contact gets the invoices now?", "invoices"),
    ("Does the SSO login still loop back for everyone?", "sso"),
    ("Why do the scheduled exports keep arriving empty?", "exports"),
    ("When exactly does their contract renew?", "renewal"),
]


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
def account(client: TestClient) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={"email": f"k-{unique}@example.com", "password": "a-strong-password", "organization_name": f"K {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Keywords"}, headers=auth).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "k", "scopes": ["events:write", "memory:read", "memory:write", "customers:read", "customers:write"]},
        headers=auth,
    ).json()["api_key"]
    return {"auth": auth, "project_id": project["id"], "key": key}


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def settings(client, account, **values) -> None:
    response = client.put(f"/v1/projects/{account['project_id']}/settings", json={"settings": values}, headers=account["auth"])
    assert response.status_code == 200, response.text


@pytest.fixture(scope="module")
def memories(client, account) -> dict[str, str]:
    client.post("/v1/customers", json={"external_id": "cus_k"}, headers=h(account["key"]))
    ids = {}
    for label, (memory_type, content) in MEMORIES.items():
        created = client.post(
            "/v1/memories", json={"customer_id": "cus_k", "content": content, "type": memory_type}, headers=h(account["key"])
        )
        assert created.status_code == 201, created.text
        ids[label] = created.json()["id"]
    return ids


def test_keyword_search_finds_what_a_longer_question_is_about(client, account, memories):
    settings(client, account, concept_retrieval=False)
    found = client.post(
        "/v1/memory/search", json={"customer_id": "cus_k", "query": "Are the webhook retries still failing today?"}, headers=h(account["key"])
    ).json()
    top = found["results"][0] if "results" in found else found["memories"][0]
    assert top["id"] == memories["webhook"]
    assert "keyword" in (top.get("retrieved_by") or top.get("strategies") or [])


def test_the_gain_is_measured_not_asserted(client, account, memories):
    eval_set = client.post("/v1/evals", json={"name": "Longer questions"}, headers=h(account["key"])).json()["id"]
    client.post(
        f"/v1/evals/{eval_set}/cases",
        json={
            "cases": [
                {"customer_id": "cus_k", "question": question, "expected_memory_ids": [memories[label]]}
                for question, label in QUESTIONS
            ]
        },
        headers=h(account["key"]),
    )
    # Concepts off in both runs, so the difference is the keyword leg's alone.
    settings(client, account, concept_retrieval=False, keyword_match_any=False)
    every = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "every word", "k": 3}, headers=h(account["key"])).json()
    settings(client, account, concept_retrieval=False, keyword_match_any=True)
    any_word = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "any word", "k": 3}, headers=h(account["key"])).json()

    assert every["settings"]["keyword_match_any"] is False and any_word["settings"]["keyword_match_any"] is True
    before, after = every["metrics"], any_word["metrics"]
    # No regression on what the set measures — recall, rank, and what the answer cites.
    assert after["mrr"] >= before["mrr"], (before, after)
    assert after["citation_hit_rate"] >= before["citation_hit_rate"], (before, after)
    assert any_word["comparison"]["regressed"] is False


def _margin(client, account, memories) -> float:
    """How far, on average, each target ranks ahead of its closest distractor."""
    labels = {ident: label for label, ident in memories.items()}
    gaps = []
    for question, label in QUESTIONS:
        found = client.post(
            "/v1/memory/search", json={"customer_id": "cus_k", "query": question, "limit": 10}, headers=h(account["key"])
        ).json()
        rows = found.get("results") or found.get("memories") or []
        scores = {labels.get(row["id"]): row["score"] for row in rows}
        target = scores.get(label, 0.0)
        rival = max((score for name, score in scores.items() if name != label), default=0.0)
        gaps.append(target - rival)
    return sum(gaps) / len(gaps)


def test_matching_any_word_widens_the_lead_over_distractors(client, account, memories):
    """Where rank metrics are at their ceiling, the lead is what any-word buys: a target
    further ahead of memories that share its vocabulary is one a small change will not
    push out of first place."""
    settings(client, account, concept_retrieval=False, keyword_match_any=False)
    every = _margin(client, account, memories)
    settings(client, account, concept_retrieval=False, keyword_match_any=True)
    any_word = _margin(client, account, memories)
    assert any_word > every, (every, any_word)
