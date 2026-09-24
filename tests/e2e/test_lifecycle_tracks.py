"""Lifecycle tracks over HTTP (§26 4.2): several machines, each with reasons in words."""

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
        json={"email": f"tracks-{unique}@example.com", "password": "a-strong-password", "organization_name": f"T {unique}"},
    )
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Tracks"}, headers=auth).json()
    key = client.post(
        f"/v1/projects/{project['id']}/api-keys", json={"name": "k", "scopes": SCOPES}, headers=auth
    ).json()["api_key"]
    return {"auth": auth, "base": f"/v1/projects/{project['id']}", "key": key}


def h(world) -> dict:
    return {"X-API-Key": world["key"]}


def test_a_new_project_starts_with_the_engagement_and_commercial_tracks(client, world):
    lifecycle = client.get("/v1/lifecycle", headers=h(world)).json()
    assert lifecycle["enabled"] is True
    tracks = {track["track"]: track for track in lifecycle["tracks"]}
    assert set(tracks) == {"engagement", "commercial"}
    assert tracks["engagement"]["states"][0] == "new"
    assert tracks["commercial"]["label"] == "Commercial"
    templates = client.get("/v1/lifecycle/templates", headers=h(world)).json()
    assert {item["name"] for item in templates} == {"engagement", "commercial"}


def test_every_track_places_the_customer_and_explains_itself(client, world):
    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world))
    client.post(
        "/v1/memories",
        json={"customer_id": "acme", "content": "The customer upgraded from the Starter plan to the Pro plan.", "type": "subscription"},
        headers=h(world),
    )
    refreshed = client.post("/v1/customers/acme/state/refresh", headers=h(world))
    assert refreshed.status_code == 200, refreshed.text
    tracks = refreshed.json()["tracks"]
    assert set(tracks) == {"lifecycle", "engagement", "commercial"}
    # An upgrade is two steps on the commercial track in one evaluation.
    assert [step["to"] for step in tracks["commercial"]["transitions"]] == ["paying", "expanding"]

    state = client.get("/v1/customers/acme/state", headers=h(world)).json()
    by_track = {track["track"]: track for track in state["tracks"]}
    assert by_track["lifecycle"]["primary"] is True
    commercial = by_track["commercial"]["current"]
    assert commercial["state"] == "expanding"
    assert commercial["track"] == "commercial"
    assert "subscription upgraded" in commercial["reasons"]
    history = client.get("/v1/customers/acme/state/history", params={"track": "commercial"}, headers=h(world)).json()
    converted = next(row for row in history["data"] if row["state"] == "paying")
    assert "on the pro plan" in converted["reasons"]

    # A track's state is a fact every rule can read.
    evaluated = client.post(
        "/v1/conditions/evaluate",
        json={"customer_id": "acme", "condition": 'lifecycle.commercial == "expanding"'},
        headers=h(world),
    ).json()
    assert evaluated["evaluation"]["matched"] is True


def test_a_track_is_set_pinned_and_released_on_its_own(client, world):
    pinned = client.put(
        "/v1/customers/acme/state",
        json={"state": "power_user", "track": "engagement", "note": "Runs our biggest rollout"},
        headers=h(world),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["track"] == "engagement" and pinned.json()["pinned"] is True
    assert pinned.json()["reasons"] == ["Runs our biggest rollout"]

    # The primary track is untouched by a pin on another.
    state = client.get("/v1/customers/acme/state", headers=h(world)).json()
    assert {t["track"]: t for t in state["tracks"]}["engagement"]["current"]["state"] == "power_user"
    assert state["current"]["track"] == "lifecycle"

    history = client.get("/v1/customers/acme/state/history", params={"track": "all"}, headers=h(world)).json()
    assert {row["track"] for row in history["data"]} >= {"lifecycle", "engagement", "commercial"}
    engagement_only = client.get(
        "/v1/customers/acme/state/history", params={"track": "engagement"}, headers=h(world)
    ).json()
    assert {row["track"] for row in engagement_only["data"]} == {"engagement"}

    in_state = client.get(
        "/v1/lifecycle/customers", params={"state": "power_user", "track": "engagement"}, headers=h(world)
    ).json()
    assert [row["external_id"] for row in in_state["data"]] == ["acme"]

    released = client.delete("/v1/customers/acme/state/pin", params={"track": "engagement"}, headers=h(world))
    assert released.status_code == 200 and released.json()["pinned"] is False


def test_unknown_tracks_and_states_are_refused(client, world):
    assert client.put(
        "/v1/customers/acme/state", json={"state": "paying", "track": "sales"}, headers=h(world)
    ).status_code == 422
    assert client.put(
        "/v1/customers/acme/state", json={"state": "renewing", "track": "engagement"}, headers=h(world)
    ).status_code == 422
    assert client.get(
        "/v1/lifecycle/customers", params={"state": "paying", "track": "nope"}, headers=h(world)
    ).status_code == 422


def test_a_broken_track_is_refused_when_saved(client, world):
    response = client.put(
        f"{world['base']}/settings",
        json={"settings": {"lifecycle_tracks": {"usage": {"states": ["low", "high"], "transitions": [
            {"to": "high", "when": "activty.events_recent > 5"}
        ]}}}},
        headers=world["auth"],
    )
    assert response.status_code == 422
    assert "activity.events_recent" in response.text


def test_the_webhook_carries_the_track_and_the_reasons(client, world):
    client.post(
        f"{world['base']}/webhooks",
        json={"url": "https://hooks.example.com/state", "event_types": ["customer.state_changed"]},
        headers=world["auth"],
    )
    client.post("/v1/customers", json={"external_id": "globex"}, headers=h(world))
    client.post(
        "/v1/memories",
        json={"customer_id": "globex", "content": "The customer cancelled their subscription.", "type": "subscription"},
        headers=h(world),
    )
    client.post("/v1/customers/globex/state/refresh", headers=h(world))
    deliveries = client.get(f"{world['base']}/webhooks/deliveries", headers=world["auth"]).json()["data"]
    payloads = [item["payload"]["data"] for item in deliveries if item["event_type"] == "customer.state_changed"]
    churned = [data for data in payloads if data["state"] == "churned" and data["track"] == "commercial"]
    assert churned, payloads
    assert "subscription cancelled" in churned[0]["reasons"]


def test_an_existing_project_without_tracks_runs_only_the_primary(client, world):
    cleared = client.put(f"{world['base']}/settings", json={"settings": {"lifecycle_tracks": {}}}, headers=world["auth"])
    assert cleared.status_code == 200, cleared.text
    client.post("/v1/customers", json={"external_id": "initech"}, headers=h(world))
    refreshed = client.post("/v1/customers/initech/state/refresh", headers=h(world)).json()
    assert set(refreshed["tracks"]) == {"lifecycle"}
    assert client.get("/v1/lifecycle", headers=h(world)).json()["tracks"] == []
