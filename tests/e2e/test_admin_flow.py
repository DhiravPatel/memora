"""API keys, team management, webhooks and customer merging, end to end over HTTP.

These are the operations a customer's platform team performs, and every one of them has a
failure mode that matters: a key that outlives its purpose, an organization that loses its
last owner, a webhook that fires without a signature, a merge that loses memories.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from tests.conftest import database_required

from app.main import create_app
from common.settings import get_settings
from database import models  # noqa: F401  (registers tables)
from database.base import Base
from webhooks import verify

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
def owner(client: TestClient) -> dict[str, str]:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": f"owner-{unique}@example.com",
            "password": "a-strong-password",
            "name": "Owner",
            "organization_name": f"Admin Co {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    body = signup.json()
    token = body["tokens"]["access_token"]

    project = client.post(
        "/v1/projects", json={"name": "Production"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    return {
        "email": body["user"]["email"],
        "token": token,
        "project_id": project["id"],
        "api_key": project["api_key"],
        "user_id": body["user"]["id"],
    }


def auth(owner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {owner['token']}"}


# ----------------------------------------------------------------- API keys


def test_project_creation_registers_a_listable_key(client, owner):
    keys = client.get(f"/v1/projects/{owner['project_id']}/api-keys", headers=auth(owner)).json()
    assert len(keys) == 1
    assert keys[0]["name"] == "Default key"
    assert keys[0]["scopes"] == ["admin"]
    assert "api_key" not in keys[0]  # the raw value is never re-served


def test_scoped_key_can_only_do_what_it_was_granted(client, owner):
    created = client.post(
        f"/v1/projects/{owner['project_id']}/api-keys",
        json={"name": "Ingestion only", "scopes": ["events:write"]},
        headers=auth(owner),
    )
    assert created.status_code == 201, created.text
    ingest_key = created.json()["api_key"]
    headers = {"X-API-Key": ingest_key}

    accepted = client.post(
        "/v1/events",
        json={"customer_id": "cus_scope", "event_type": "feature_used", "data": {"feature": "x"}},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text

    # The same key must not be able to read memories back.
    denied = client.post(
        "/v1/memory/query",
        json={"customer_id": "cus_scope", "query": "what problems?"},
        headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "authorization_error"
    assert "memory:read" in denied.json()["error"]["message"]


def test_key_usage_is_tracked(client, owner):
    keys = client.get(f"/v1/projects/{owner['project_id']}/api-keys", headers=auth(owner)).json()
    ingestion = next(key for key in keys if key["name"] == "Ingestion only")
    assert ingestion["use_count"] >= 1
    assert ingestion["last_used_at"]


def test_scopes_can_be_widened_after_the_fact(client, owner):
    keys = client.get(f"/v1/projects/{owner['project_id']}/api-keys", headers=auth(owner)).json()
    ingestion = next(key for key in keys if key["name"] == "Ingestion only")

    updated = client.patch(
        f"/v1/projects/{owner['project_id']}/api-keys/{ingestion['id']}",
        json={"scopes": ["events:write", "memory:read", "customers:read"]},
        headers=auth(owner),
    )
    assert updated.status_code == 200
    assert "memory:read" in updated.json()["scopes"]


def test_revoked_key_stops_working_immediately(client, owner):
    created = client.post(
        f"/v1/projects/{owner['project_id']}/api-keys",
        json={"name": "Temporary", "scopes": ["customers:read"], "expires_in_days": 1},
        headers=auth(owner),
    ).json()
    temporary = created["api_key"]

    assert client.get("/v1/customers", headers={"X-API-Key": temporary}).status_code == 200

    revoked = client.delete(
        f"/v1/projects/{owner['project_id']}/api-keys/{created['id']}", headers=auth(owner)
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"]

    after = client.get("/v1/customers", headers={"X-API-Key": temporary})
    assert after.status_code == 401
    assert "revoked" in after.json()["error"]["message"].lower()


def test_last_active_key_cannot_be_revoked(client, owner):
    """Locking yourself out of your own project is not a supported operation."""
    unique = str(int(time.time() * 1000))
    solo = client.post(
        "/v1/projects", json={"name": f"Solo {unique}"}, headers=auth(owner)
    ).json()
    keys = client.get(f"/v1/projects/{solo['id']}/api-keys", headers=auth(owner)).json()

    response = client.delete(
        f"/v1/projects/{solo['id']}/api-keys/{keys[0]['id']}", headers=auth(owner)
    )
    assert response.status_code == 409
    assert "last active key" in response.json()["error"]["message"]


def test_unknown_scope_is_rejected(client, owner):
    response = client.post(
        f"/v1/projects/{owner['project_id']}/api-keys",
        json={"name": "Bad", "scopes": ["everything"]},
        headers=auth(owner),
    )
    assert response.status_code == 422


# --------------------------------------------------------------------- team


def test_invitation_flow(client, owner):
    unique = str(int(time.time() * 1000))
    email = f"member-{unique}@example.com"

    invited = client.post(
        "/v1/organization/invitations",
        json={"email": email, "role": "member"},
        headers=auth(owner),
    )
    assert invited.status_code == 201, invited.text
    invitation = invited.json()
    assert invitation["status"] == "pending"
    assert invitation["accept_path"].startswith("/accept-invitation?token=")

    listed = client.get("/v1/organization/invitations", headers=auth(owner)).json()
    assert any(item["email"] == email for item in listed)

    accepted = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invitation["token"], "password": "member-password", "name": "New Member"},
    )
    assert accepted.status_code == 201, accepted.text
    member_token = accepted.json()["tokens"]["access_token"]

    # The new member sees the same organization, and their role is what was invited.
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {member_token}"}).json()
    assert me["role"] == "member"
    assert me["organization_id"] == accepted.json()["organization"]["id"]

    # A single-use token cannot be replayed.
    replay = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invitation["token"], "password": "another-password"},
    )
    assert replay.status_code in (409, 422)


def test_an_invitation_can_be_revoked(client, owner):
    """Revoking is a separate request, so the row is *loaded* rather than freshly built.

    That distinction is the whole test: the status column is a String, so a loaded row
    gives a plain ``str``, and an identity check against the enum member silently fails.
    Every revoke did, until this test existed.
    """
    unique = str(int(time.time() * 1000))
    invited = client.post(
        "/v1/organization/invitations",
        json={"email": f"revoked-{unique}@example.com", "role": "viewer"},
        headers=auth(owner),
    ).json()

    revoked = client.delete(
        f"/v1/organization/invitations/{invited['id']}", headers=auth(owner)
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    # The link is dead, and revoking again says so rather than pretending to work.
    accepted = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invited["token"], "password": "a-strong-password"},
    )
    assert accepted.status_code in (409, 422)

    again = client.delete(f"/v1/organization/invitations/{invited['id']}", headers=auth(owner))
    assert again.status_code == 409
    assert "already revoked" in again.json()["error"]["message"].lower()


def test_members_are_listed_with_roles(client, owner):
    members = client.get("/v1/organization/members", headers=auth(owner)).json()
    roles = {member["email"]: member["role"] for member in members}
    assert roles[owner["email"]] == "owner"
    assert any(role == "member" for role in roles.values())


def test_member_cannot_invite_above_their_own_role(client, owner):
    unique = str(int(time.time() * 1000))
    invitation = client.post(
        "/v1/organization/invitations",
        json={"email": f"admin-{unique}@example.com", "role": "admin"},
        headers=auth(owner),
    ).json()
    accepted = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invitation["token"], "password": "admin-password"},
    ).json()
    admin_headers = {"Authorization": f"Bearer {accepted['tokens']['access_token']}"}

    response = client.post(
        "/v1/organization/invitations",
        json={"email": f"owner2-{unique}@example.com", "role": "owner"},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "above your own" in response.json()["error"]["message"]


def test_last_owner_cannot_be_demoted_or_removed(client, owner):
    response = client.patch(
        f"/v1/organization/members/{owner['user_id']}",
        json={"role": "member"},
        headers=auth(owner),
    )
    assert response.status_code == 422  # cannot demote yourself

    removal = client.delete(
        f"/v1/organization/members/{owner['user_id']}", headers=auth(owner)
    )
    assert removal.status_code == 422  # cannot remove yourself


def test_removed_member_loses_access(client, owner):
    unique = str(int(time.time() * 1000))
    invitation = client.post(
        "/v1/organization/invitations",
        json={"email": f"temp-{unique}@example.com", "role": "viewer"},
        headers=auth(owner),
    ).json()
    accepted = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invitation["token"], "password": "temp-password"},
    ).json()
    temp_headers = {"Authorization": f"Bearer {accepted['tokens']['access_token']}"}
    assert client.get("/v1/auth/me", headers=temp_headers).status_code == 200

    removed = client.delete(
        f"/v1/organization/members/{accepted['user']['id']}", headers=auth(owner)
    )
    assert removed.status_code == 200
    assert client.get("/v1/auth/me", headers=temp_headers).status_code == 401


def test_password_change_invalidates_other_sessions(client, owner):
    unique = str(int(time.time() * 1000))
    email = f"rotator-{unique}@example.com"
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "first-password",
            "organization_name": f"Rotate {unique}",
        },
    ).json()
    old_headers = {"Authorization": f"Bearer {signup['tokens']['access_token']}"}

    changed = client.post(
        "/v1/auth/change-password",
        json={"current_password": "first-password", "new_password": "second-password"},
        headers=old_headers,
    )
    assert changed.status_code == 200
    new_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    assert client.get("/v1/auth/me", headers=old_headers).status_code == 401
    assert client.get("/v1/auth/me", headers=new_headers).status_code == 200
    assert client.post(
        "/v1/auth/login", json={"email": email, "password": "second-password"}
    ).status_code == 200


# ----------------------------------------------------------------- webhooks


def test_webhook_endpoint_lifecycle_and_signed_payload(client, owner):
    created = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks",
        json={
            "url": "http://localhost:9999/hooks/memora",
            "description": "Test receiver",
            "event_types": ["customer.at_risk", "memory.created"],
        },
        headers=auth(owner),
    )
    assert created.status_code == 201, created.text
    endpoint = created.json()
    secret = endpoint["secret"]
    assert secret.startswith("whsec_")
    assert endpoint["is_active"] is True

    # A test delivery is queued, signed with that secret, and visible in the log.
    test = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks/{endpoint['id']}/test", headers=auth(owner)
    )
    assert test.status_code == 202

    deliveries = client.get(
        f"/v1/projects/{owner['project_id']}/webhooks/deliveries", headers=auth(owner)
    ).json()
    assert deliveries["total"] >= 1
    delivery = deliveries["data"][0]
    assert delivery["status"] in ("pending", "succeeded", "failed")
    assert delivery["payload"]["type"] == "customer.health_changed"

    # The receiver-side check: our signature verifies against the payload we queued.
    body = json.dumps(delivery["payload"], default=str, separators=(",", ":")).encode()
    from webhooks import sign

    header, _ = sign(secret=secret, body=body)
    assert verify(secret=secret, body=body, header=header)

    rotated = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks/{endpoint['id']}/rotate-secret",
        headers=auth(owner),
    ).json()
    assert rotated["secret"] != secret

    disabled = client.patch(
        f"/v1/projects/{owner['project_id']}/webhooks/{endpoint['id']}",
        json={"is_active": False},
        headers=auth(owner),
    ).json()
    assert disabled["is_active"] is False


def test_the_webhook_secret_is_not_readable_in_the_database(client, owner):
    """A database dump must not be enough to forge a delivery.

    Skipped when the deployment has not configured an encryption key — that is a supported
    (non-production) state, and the column type then stores the value as it always did.
    """
    from sqlalchemy import create_engine, text

    from common.settings import get_settings as _settings

    if not _settings().encryption_enabled:
        pytest.skip("SECRETS_ENCRYPTION_KEY is not configured in this environment.")

    created = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks",
        json={"url": "http://localhost:9997/hooks/encrypted"},
        headers=auth(owner),
    )
    assert created.status_code == 201, created.text
    endpoint = created.json()
    secret = endpoint["secret"]

    engine = create_engine(_settings().sync_database_url)
    try:
        with engine.connect() as connection:
            stored = connection.execute(
                text("SELECT secret FROM webhook_endpoints WHERE id = :id"),
                {"id": endpoint["id"]},
            ).scalar_one()
    finally:
        engine.dispose()

    assert stored != secret
    assert secret not in stored
    assert stored.startswith("enc:v1:")

    # And the application still signs with the real value.
    from webhooks import sign
    from webhooks import verify as verify_signature

    header, _ = sign(secret=secret, body=b"{}")
    assert verify_signature(secret=secret, body=b"{}", header=header)


def test_pipeline_emits_webhooks_for_subscribed_events(client, owner):
    client.post(
        f"/v1/projects/{owner['project_id']}/webhooks",
        json={"url": "http://localhost:9998/hooks/all"},
        headers=auth(owner),
    )

    before = client.get(
        f"/v1/projects/{owner['project_id']}/webhooks/deliveries",
        params={"limit": 1},
        headers=auth(owner),
    ).json()["total"]

    client.post(
        "/v1/events",
        json={
            "customer_id": "cus_webhook_flow",
            "event_type": "support_message",
            "data": {"message": "Shopify has failed three times and we may cancel."},
            "external_event_id": "wh-1",
        },
        headers={"X-API-Key": owner["api_key"]},
    )

    after = client.get(
        f"/v1/projects/{owner['project_id']}/webhooks/deliveries",
        params={"limit": 50},
        headers=auth(owner),
    ).json()
    assert after["total"] > before
    # customer.created fires on first sight, without any processing.
    assert any(item["event_type"] == "customer.created" for item in after["data"])


def test_webhook_events_and_scopes_are_discoverable(client, owner):
    events = client.get(
        f"/v1/projects/{owner['project_id']}/webhooks/events", headers=auth(owner)
    ).json()
    assert {item["event"] for item in events} >= {"memory.created", "customer.at_risk"}
    assert all(item["description"] for item in events)

    scopes = client.get(
        f"/v1/projects/{owner['project_id']}/api-keys/scopes", headers=auth(owner)
    ).json()
    assert {item["scope"] for item in scopes} >= {"events:write", "memory:read", "admin"}


def test_duplicate_webhook_url_is_rejected(client, owner):
    payload = {"url": "http://localhost:9997/hooks/dupe"}
    first = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks", json=payload, headers=auth(owner)
    )
    second = client.post(
        f"/v1/projects/{owner['project_id']}/webhooks", json=payload, headers=auth(owner)
    )
    assert first.status_code == 201
    assert second.status_code == 409


# ------------------------------------------------------------------ settings


def test_settings_endpoint_returns_values_schema_and_defaults(client, owner):
    response = client.get(f"/v1/projects/{owner['project_id']}/settings", headers=auth(owner))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["project_id"] == owner["project_id"]
    assert {group["key"] for group in body["groups"]} >= {"extraction", "retrieval", "privacy"}
    keys = {field["key"] for field in body["fields"]}
    assert {"min_event_importance", "ranking_weights", "retention", "pii_redaction_enabled"} <= keys
    # Every field carries what a form needs to render itself.
    for field in body["fields"]:
        assert field["label"] and field["help"] and field["kind"]
    assert body["values"]["ranking_weights"]["similarity"] > 0
    assert body["defaults"]["min_event_importance"] == body["values"]["min_event_importance"]


def test_settings_are_validated_on_write(client, owner):
    bad = client.put(
        f"/v1/projects/{owner['project_id']}/settings",
        json={"settings": {"min_event_importance": 5}},
        headers=auth(owner),
    )
    assert bad.status_code == 422
    assert "at most 1" in bad.json()["error"]["message"]

    # And the project is untouched.
    values = client.get(
        f"/v1/projects/{owner['project_id']}/settings", headers=auth(owner)
    ).json()["values"]
    assert values["min_event_importance"] != 5


def test_settings_round_trip_and_partial_nesting(client, owner):
    saved = client.put(
        f"/v1/projects/{owner['project_id']}/settings",
        json={
            "settings": {
                "min_event_importance": 0.42,
                "decay_days": 120,
                "ranking_weights": {"recency": 0.3, "similarity": 0.3, "importance": 0.2,
                                    "confidence": 0.1, "relationship": 0.1},
                "event_importance": {"trial_extended": 0.9},
                "pii_redaction_enabled": False,
            }
        },
        headers=auth(owner),
    )
    assert saved.status_code == 200, saved.text
    values = saved.json()["values"]
    assert values["min_event_importance"] == 0.42
    assert values["decay_days"] == 120
    assert values["ranking_weights"]["recency"] == 0.3
    assert values["event_importance"]["trial_extended"] == 0.9
    assert values["pii_redaction_enabled"] is False

    # Reading it back gives the same effective values.
    reread = client.get(
        f"/v1/projects/{owner['project_id']}/settings", headers=auth(owner)
    ).json()["values"]
    assert reread["min_event_importance"] == 0.42
    assert reread["retention"]["events_days"] == 90  # untouched default survives


def test_settings_writes_require_admin(client, owner):
    unique = str(int(time.time() * 1000))
    invitation = client.post(
        "/v1/organization/invitations",
        json={"email": f"viewer-{unique}@example.com", "role": "viewer"},
        headers=auth(owner),
    ).json()
    accepted = client.post(
        "/v1/auth/accept-invitation",
        json={"token": invitation["token"], "password": "viewer-password"},
    ).json()
    viewer = {"Authorization": f"Bearer {accepted['tokens']['access_token']}"}

    # A viewer can read the configuration but not change it.
    assert client.get(
        f"/v1/projects/{owner['project_id']}/settings", headers=viewer
    ).status_code == 200
    denied = client.put(
        f"/v1/projects/{owner['project_id']}/settings",
        json={"settings": {"decay_days": 30}},
        headers=viewer,
    )
    assert denied.status_code == 403


def test_settings_changes_are_audited(client, owner):
    logs = client.get(
        f"/v1/projects/{owner['project_id']}/audit-logs",
        params={"limit": 100},
        headers=auth(owner),
    ).json()
    assert any(
        entry["resource_type"] == "project_settings" and entry["action"] == "configuration_change"
        for entry in logs
    )


def test_project_rename_rejects_duplicates(client, owner):
    unique = str(int(time.time() * 1000))
    other = client.post(
        "/v1/projects", json={"name": f"Rename target {unique}"}, headers=auth(owner)
    ).json()

    clash = client.patch(
        f"/v1/projects/{owner['project_id']}", json={"name": other["name"]}, headers=auth(owner)
    )
    assert clash.status_code == 409

    renamed = client.patch(
        f"/v1/projects/{owner['project_id']}",
        json={"name": f"Production {unique}"},
        headers=auth(owner),
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == f"Production {unique}"


# ---------------------------------------------------------------- customers


def test_bulk_upsert_and_merge(client, owner):
    headers = {"X-API-Key": owner["api_key"]}

    bulk = client.post(
        "/v1/customers/batch",
        json={
            "customers": [
                {"external_id": "cus_dupe_a", "name": "Jane A", "email": "jane@example.com"},
                {"external_id": "cus_dupe_b", "name": "Jane B"},
            ]
        },
        headers=headers,
    )
    assert bulk.status_code == 201, bulk.text
    assert bulk.json()["created"] == 2

    for external_id in ("cus_dupe_a", "cus_dupe_b"):
        client.post(
            "/v1/events",
            json={
                "customer_id": external_id,
                "event_type": "support_message",
                "data": {"message": f"The export feature is failing for {external_id}."},
                "external_event_id": f"merge-{external_id}",
            },
            headers=headers,
        )

    merged = client.post(
        "/v1/customers/cus_dupe_b/merge",
        json={"into": "cus_dupe_a"},
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    result = merged.json()
    assert result["events_moved"] >= 1

    # The survivor holds both histories; the merged id is gone from the active list.
    survivor = client.get("/v1/customers/cus_dupe_a", headers=headers).json()
    assert survivor["external_id"] == "cus_dupe_a"
    events = client.get(
        "/v1/events", params={"customer_id": "cus_dupe_a", "limit": 50}, headers=headers
    ).json()
    assert events["total"] >= 2
    assert client.get("/v1/customers/cus_dupe_b", headers=headers).status_code == 404


def test_merge_into_self_is_rejected(client, owner):
    response = client.post(
        "/v1/customers/cus_dupe_a/merge",
        json={"into": "cus_dupe_a"},
        headers={"X-API-Key": owner["api_key"]},
    )
    assert response.status_code == 409


def test_health_is_stored_on_the_customer(client, owner):
    """Health is computed during processing and persisted, not recomputed per request."""
    from tests.e2e.test_api_flow import _process_pending

    _process_pending(client, {"api_key": owner["api_key"]})
    health = client.get(
        "/v1/customers/cus_webhook_flow/health", headers={"X-API-Key": owner["api_key"]}
    )
    assert health.status_code == 200
    assert health.json()["band"] in ("healthy", "watch", "at_risk", "critical")
