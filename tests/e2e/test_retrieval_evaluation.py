"""Retrieval evaluation over HTTP: sets, cases, runs, and the comparison between runs."""

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
        json={"email": f"eval-{unique}@example.com", "password": "a-strong-password", "organization_name": f"Eval {unique}"},
    )
    assert signup.status_code == 201, signup.text
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Eval"}, headers=auth).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "k", "scopes": ["memory:read", "memory:write", "customers:read", "customers:write"]},
        headers=auth,
    ).json()["api_key"]
    reader = client.post(
        f"/v1/projects/{project['id']}/api-keys",
        json={"name": "r", "scopes": ["memory:read", "customers:read"]},
        headers=auth,
    ).json()["api_key"]
    return {"auth": auth, "project_id": project["id"], "key": key, "reader": reader}


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def customer(client: TestClient, account: dict) -> dict:
    client.post("/v1/customers", json={"external_id": "cus_eval"}, headers=h(account["key"]))
    ids = {}
    for label, memory_type, content in (
        ("shopify", "problem", "The Shopify sync fails during checkout and orders are lost."),
        ("billing", "problem", "The customer was charged twice for the March invoice."),
        ("channel", "preference", "The customer prefers to be contacted on WhatsApp."),
    ):
        response = client.post(
            "/v1/memories",
            json={"customer_id": "cus_eval", "content": content, "type": memory_type},
            headers=h(account["key"]),
        )
        assert response.status_code == 201, response.text
        ids[label] = response.json()["id"]
    return ids


@pytest.fixture(scope="module")
def eval_set(client: TestClient, account: dict, customer: dict) -> str:
    created = client.post("/v1/evals", json={"name": "Support questions"}, headers=h(account["key"]))
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    cases = client.post(
        f"/v1/evals/{set_id}/cases",
        json={
            "cases": [
                {"customer_id": "cus_eval", "question": "Is there a problem with Shopify?", "expected_memory_ids": [customer["shopify"]]},
                {"customer_id": "cus_eval", "question": "Were they billed incorrectly?", "expected_phrases": ["charged twice"]},
                {"customer_id": "cus_eval", "question": "How should we contact them?", "expected_phrases": ["whatsapp"]},
            ]
        },
        headers=h(account["key"]),
    )
    assert cases.status_code == 201, cases.text
    return set_id


def test_a_set_is_created_with_its_cases(client, account, eval_set):
    detail = client.get(f"/v1/evals/{eval_set}", headers=h(account["key"])).json()
    assert detail["cases"] == 3
    assert detail["case_list"][0]["customer_id"] == "cus_eval"


def test_a_duplicate_name_is_refused(client, account, eval_set):
    response = client.post("/v1/evals", json={"name": "support questions"}, headers=h(account["key"]))
    assert response.status_code == 409


def test_a_case_needs_to_say_what_the_right_answer_is(client, account, eval_set):
    response = client.post(
        f"/v1/evals/{eval_set}/cases",
        json={"cases": [{"customer_id": "cus_eval", "question": "Anything?"}]},
        headers=h(account["key"]),
    )
    assert response.status_code == 422
    assert "expected" in response.json()["error"]["message"]


def test_an_expected_memory_must_belong_to_the_customer(client, account, eval_set):
    response = client.post(
        f"/v1/evals/{eval_set}/cases",
        json={"cases": [{"customer_id": "cus_eval", "question": "?", "expected_memory_ids": ["mem_nope"]}]},
        headers=h(account["key"]),
    )
    assert response.status_code == 422
    assert "mem_nope" in response.json()["error"]["message"]


def test_a_run_scores_every_question(client, account, eval_set):
    response = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "baseline"}, headers=h(account["key"]))
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    metrics = run["metrics"]
    assert metrics["cases"] == 3 and metrics["scored"] == 3
    assert metrics["hit"]["@10"] == 1.0, "every question has its answer somewhere in the top 10"
    assert 0 < metrics["mrr"] <= 1
    assert run["comparison"] is None, "the first run has nothing to compare with"
    result = run["results"][0]
    assert result["expected"][0]["rank"] is not None
    assert result["retrieved"], "per-question detail says what came back, and how"
    assert run["settings"]["engine"]


def test_a_second_run_is_compared_with_the_first(client, account, eval_set):
    second = client.post(f"/v1/evals/{eval_set}/runs", json={"label": "again"}, headers=h(account["key"])).json()
    assert second["comparison"] is not None
    assert second["comparison"]["recall"]["@10"] == 0.0, "nothing changed, so nothing moved"
    assert second["comparison"]["regressed"] is False


def test_evaluation_does_not_write_to_the_query_log(client, account, eval_set):
    """Synthetic questions are neither traffic nor billable usage."""
    logged = client.get(f"/v1/projects/{account['project_id']}/queries", headers=account["auth"])
    assert logged.status_code == 200, logged.text
    questions = [row["query"] for row in logged.json()]
    assert not any("Is there a problem with Shopify" in question for question in questions)


def test_runs_are_listed_newest_first(client, account, eval_set):
    runs = client.get(f"/v1/evals/{eval_set}/runs", headers=h(account["key"])).json()
    assert [run["label"] for run in runs][:2] == ["again", "baseline"]
    listed = client.get("/v1/evals", headers=h(account["key"])).json()
    assert listed[0]["latest_run"]["label"] == "again"


def test_a_read_only_key_cannot_start_a_run(client, account, eval_set):
    response = client.post(f"/v1/evals/{eval_set}/runs", json={}, headers=h(account["reader"]))
    assert response.status_code == 403


def test_suggestions_come_from_real_questions(client, account, customer):
    asked = client.post(
        "/v1/memory/query",
        json={"customer_id": "cus_eval", "query": "What went wrong with checkout?"},
        headers=h(account["key"]),
    )
    assert asked.status_code == 200, asked.text
    suggestions = client.get("/v1/evals/suggestions", headers=h(account["key"])).json()
    assert suggestions[0]["question"] == "What went wrong with checkout?"
    assert suggestions[0]["retrieved"], "a person picks the right answer from what came back"
