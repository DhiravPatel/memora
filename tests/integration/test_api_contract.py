"""API contract checks against the real app (no database required).

Authentication is rejected before any query runs, so these verify the security
boundary and the documented surface without infrastructure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics_are_exposed(client):
    assert client.get("/metrics").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/events"),
        ("GET", "/v1/events"),
        ("GET", "/v1/customers"),
        ("POST", "/v1/memory/query"),
        ("POST", "/v1/memory/context"),
        ("GET", "/v1/memories"),
    ],
)
def test_public_api_requires_an_api_key(client, method, path):
    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/v1/projects"), ("POST", "/v1/projects"), ("GET", "/v1/auth/me")],
)
def test_dashboard_api_requires_a_jwt(client, method, path):
    response = client.request(method, path, json={"name": "x"})
    assert response.status_code == 401


def test_authentication_runs_before_body_validation(client):
    """An unauthenticated caller must not learn anything about the request schema."""
    response = client.post("/v1/events", json={"nonsense": True})
    assert response.status_code == 401


def test_validation_errors_are_structured(client):
    response = client.post("/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"]


def test_security_headers_and_request_id(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers.get("x-request-id")


def test_openapi_documents_the_public_surface(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/v1/events",
        "/v1/customers/{customer_id}",
        "/v1/customers/{customer_id}/memories",
        "/v1/customers/{customer_id}/timeline",
        "/v1/memory/query",
        "/v1/memory/context",
    ):
        assert path in paths
