"""Agent profiles, guardrails, approvals and runs — over HTTP, against a real database.

Phase 3 of §26 from the outside. The contracts pinned here:

* a key bound to an agent profile reads only the memory types the profile allows, through
  *every* surface — lists, answers, context, facts, goals, recommendations, raw events;
* a profile can only narrow a key: its consent is needed for restricted memory on top of
  the key's clearance;
* a guardrail check is judged on everything and explained through what the caller may see;
* an approval covers exactly one request, once, can only satisfy ``require_approval``,
  and can never be decided by the agent that asked for it;
* every answer and briefing is a run that can be explained later — including when the
  memory it relied on has since been corrected.
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

SHOPIFY = "The Shopify sync keeps failing for our store."
FEEDBACK = "We love the new dashboard but reporting is slow."
PREFERENCE = "They prefer to be contacted on WhatsApp."
SALARY = "Our salary export to payroll is broken."
GOAL = "We want to launch our store in Europe by Q3."

WRITER_SCOPES = [
    "events:write",
    "memory:read",
    "memory:write",
    "customers:read",
    "customers:write",
    "memory:restricted",
]


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


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def world(client: TestClient) -> dict:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": f"agents-{unique}@example.com",
            "password": "a-strong-password",
            "organization_name": f"Agents {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    auth = {"Authorization": f"Bearer {signup.json()['tokens']['access_token']}"}
    project = client.post("/v1/projects", json={"name": "Agents"}, headers=auth)
    assert project.status_code == 201, project.text
    pid = project.json()["id"]
    base = f"/v1/projects/{pid}"

    settings = client.put(
        f"{base}/settings",
        json={
            "settings": {
                "restriction_policies": [{"kind": "term", "value": ["salary"], "label": "compensation"}],
                "guardrails": {
                    "disabled": [],
                    "rules": [
                        {
                            "name": "big refunds",
                            "actions": ["process_refund"],
                            "when": "request.amount > 500",
                            "decision": "deny",
                            "message": "Refunds over 500 go to finance.",
                        }
                    ],
                    "approval_ttl_hours": 24,
                },
            }
        },
        headers=auth,
    )
    assert settings.status_code == 200, settings.text

    support = client.post(
        f"{base}/agent/profiles",
        json={
            "name": "support-agent",
            "description": "Answers tickets",
            "readable_types": ["problem", "preference", "subscription", "fact"],
            "denied_actions": ["offer_discount"],
        },
        headers=auth,
    )
    assert support.status_code == 201, support.text
    sales = client.post(
        f"{base}/agent/profiles",
        json={"name": "sales-agent", "allowed_actions": ["offer_upgrade", "contact_customer"]},
        headers=auth,
    )
    assert sales.status_code == 201, sales.text

    def key(name: str, scopes: list[str], profile_id: str | None = None) -> str:
        created = client.post(
            f"{base}/api-keys",
            json={"name": name, "scopes": scopes, "agent_profile_id": profile_id},
            headers=auth,
        )
        assert created.status_code == 201, created.text
        return created.json()["api_key"]

    world = {
        "auth": auth,
        "base": base,
        "project_id": pid,
        "original": project.json()["api_key"],
        "writer": key("writer", WRITER_SCOPES),
        # Holds memory:restricted, but its profile does not consent: it must not be cleared.
        "support": key(
            "support",
            ["events:write", "memory:read", "customers:read", "memory:restricted"],
            support.json()["id"],
        ),
        "sales": key("sales", ["memory:read", "customers:read"], sales.json()["id"]),
        "plain": key("plain", ["memory:read", "customers:read", "events:write"]),
        "approver": key("approver", ["memory:read", "approvals:decide"]),
        "admin_only": key("admin-only", ["admin"]),
        "support_profile": support.json()["id"],
        "sales_profile": sales.json()["id"],
    }

    client.post("/v1/customers", json={"external_id": "acme", "name": "Acme"}, headers=h(world["writer"]))
    for memory_type, content in [
        ("problem", SHOPIFY),
        ("feedback", FEEDBACK),
        ("preference", PREFERENCE),
        ("problem", SALARY),
        ("goal", GOAL),
    ]:
        created = client.post(
            "/v1/memories",
            json={"customer_id": "acme", "content": content, "type": memory_type},
            headers=h(world["writer"]),
        )
        assert created.status_code == 201, created.text
        world[content] = created.json()["id"]
    _refresh_goals(pid, "acme")
    return world


def _refresh_goals(project_id: str, external_id: str) -> None:
    """Run the goal tracker inline, the way the worker does after an event."""
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database.repositories import CustomerRepository, ProjectRepository
    from memory_engine import MemoryEngine
    from nlp import LocalEmbedder

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                project = await ProjectRepository(session).get(project_id)
                customer = await CustomerRepository(session).resolve(external_id, project_id)
                await MemoryEngine(session=session, embedder=LocalEmbedder()).refresh_goals(
                    project=project, customer=customer
                )
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)


def _sql(statement: str, **params) -> None:
    engine = create_engine(get_settings().sync_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement), params)
    finally:
        engine.dispose()


# -------------------------------------------------------------------- profiles


def test_profiles_are_validated_and_keys_counted(client, world):
    base, auth = world["base"], world["auth"]
    duplicate = client.post(f"{base}/agent/profiles", json={"name": "support-agent"}, headers=auth)
    assert duplicate.status_code == 409
    both = client.post(
        f"{base}/agent/profiles",
        json={"name": "confused", "allowed_actions": ["refund"], "denied_actions": ["refund"]},
        headers=auth,
    )
    assert both.status_code == 422
    bad_name = client.post(f"{base}/agent/profiles", json={"name": "Has Spaces!"}, headers=auth)
    assert bad_name.status_code == 422

    profiles = {row["name"]: row for row in client.get(f"{base}/agent/profiles", headers=auth).json()}
    assert profiles["support-agent"]["keys"] == 1
    assert profiles["support-agent"]["denied_actions"] == ["offer_discount"]


def test_a_profile_with_keys_cannot_be_deleted(client, world):
    response = client.delete(f"{world['base']}/agent/profiles/{world['support_profile']}", headers=world["auth"])
    assert response.status_code == 409
    assert "widen" in response.json()["error"]["message"]


def test_a_key_knows_its_own_profile(client, world):
    mine = client.get("/v1/agent/profiles/me", headers=h(world["support"]))
    assert mine.status_code == 200, mine.text
    assert mine.json()["name"] == "support-agent"
    assert client.get("/v1/agent/profiles/me", headers=h(world["plain"])).json() is None


def test_an_agent_key_cannot_also_decide_approvals(client, world):
    created = client.post(
        f"{world['base']}/api-keys",
        json={"name": "sneaky", "scopes": ["memory:read", "approvals:decide"], "agent_profile_id": world["sales_profile"]},
        headers=world["auth"],
    )
    assert created.status_code == 422


# ---------------------------------------------------------- reading as a profile


def test_a_bound_key_lists_only_its_types_and_is_told_what_was_withheld(client, world):
    listed = client.get("/v1/customers/acme/memories", params={"limit": 50}, headers=h(world["support"])).json()
    types = {item["type"] for item in listed["data"]}
    assert types <= {"problem", "preference", "subscription", "fact"}
    contents = {item["content"] for item in listed["data"]}
    assert SHOPIFY in contents and PREFERENCE in contents
    # Feedback and the goal are outside the profile; the salary problem is restricted and
    # the profile does not consent, although the key holds memory:restricted.
    assert FEEDBACK not in contents and GOAL not in contents and SALARY not in contents
    assert listed["withheld"] == 3


def test_a_hidden_type_is_not_found_by_id_or_by_search(client, world):
    missing = client.get(f"/v1/memories/{world[FEEDBACK]}", headers=h(world["support"]))
    assert missing.status_code == 404
    found = client.post(
        "/v1/memory/search", json={"customer_id": "acme", "query": "dashboard reporting slow"}, headers=h(world["support"])
    ).json()
    assert world[FEEDBACK] not in {item["id"] for item in found["memories"]}


def test_answers_and_context_are_built_from_what_the_profile_may_read(client, world):
    answered = client.post(
        "/v1/memory/query",
        json={"customer_id": "acme", "query": "What feedback did they give about the dashboard?"},
        headers=h(world["support"]),
    ).json()
    assert world[FEEDBACK] not in {item["id"] for item in answered["memories"]}
    assert "reporting is slow" not in answered["answer"]

    context = client.post(
        "/v1/memory/context", json={"customer_id": "acme", "task": "reply to a ticket"}, headers=h(world["support"])
    ).json()
    assert world[FEEDBACK] not in context["customer_context"]["memory_ids"]
    assert world[GOAL] not in context["customer_context"]["memory_ids"]
    assert world[SHOPIFY] in context["customer_context"]["memory_ids"]


def test_the_profile_must_consent_to_restricted_memory(client, world):
    base, auth = world["base"], world["auth"]
    client.patch(f"{base}/agent/profiles/{world['support_profile']}", json={"can_read_restricted": True}, headers=auth)
    try:
        listed = client.get("/v1/customers/acme/memories", params={"limit": 50}, headers=h(world["support"])).json()
        assert SALARY in {item["content"] for item in listed["data"]}
    finally:
        client.patch(
            f"{base}/agent/profiles/{world['support_profile']}", json={"can_read_restricted": False}, headers=auth
        )
    listed = client.get("/v1/customers/acme/memories", params={"limit": 50}, headers=h(world["support"])).json()
    assert SALARY not in {item["content"] for item in listed["data"]}


def test_goals_born_from_a_hidden_memory_are_hidden_with_it(client, world):
    everything = client.get("/v1/customers/acme/goals", headers=h(world["writer"])).json()
    assert everything["total"] == 1
    assert client.get("/v1/customers/acme/goals", headers=h(world["support"])).json()["total"] == 0
    goal_id = everything["data"][0]["id"]
    assert client.get(f"/v1/goals/{goal_id}", headers=h(world["support"])).status_code == 404


def test_facts_and_the_360_follow_the_profile(client, world):
    facts = client.get("/v1/customers/acme/facts", headers=h(world["support"])).json()
    assert "reporting" not in (facts["values"].get("feedback.terms") or [])
    assert "europe" not in (facts["values"].get("goals.terms") or [])
    assert world[FEEDBACK] not in {i for ids in facts["evidence"].values() for i in ids}

    view = client.get("/v1/customers/acme/360", headers=h(world["support"])).json()
    shown = {item["id"] for item in view["sections"]["important_memories"]}
    assert world[FEEDBACK] not in shown and world[GOAL] not in shown
    assert view["sections"]["goals"] == []
    assert view["withheld"] == 3


def test_recommendations_keep_the_advice_but_not_the_hidden_words(client, world):
    client.post("/v1/customers", json={"external_id": "globex"}, headers=h(world["writer"]))
    created = client.post(
        "/v1/memories",
        json={"customer_id": "globex", "content": "Salary payments to staff keep failing.", "type": "problem"},
        headers=h(world["writer"]),
    ).json()
    cleared = client.get("/v1/customers/globex/recommendations", headers=h(world["writer"])).json()
    resolve = next(item for item in cleared["recommendations"] if item["key"] == "resolve_open_problem")
    assert "Salary" in resolve["action"]

    shown = client.get("/v1/customers/globex/recommendations", headers=h(world["plain"])).json()
    resolve = next(item for item in shown["recommendations"] if item["key"] == "resolve_open_problem")
    assert resolve["action"] == "Resolve: [withheld]"
    assert created["id"] not in resolve["memory_ids"]


# ------------------------------------------------------------------ guardrails


def check(client, key: str, **body) -> dict:
    response = client.post("/v1/agent/check", json={"customer_id": "acme", **body}, headers=h(key))
    assert response.status_code == 200, response.text
    return response.json()


def test_an_open_problem_blocks_an_upsell_and_cites_only_what_the_caller_may_see(client, world):
    verdict = check(client, world["sales"], action="offer_upgrade")
    assert verdict["decision"] == "deny"
    assert not verdict["allowed"]
    rules = {reason["rule"] for reason in verdict["reasons"]}
    assert "open_problem_blocks_selling" in rules
    # Both open problems count — a restricted problem still stops the sale — but the
    # sales key may only be shown the one it can read.
    assert "2 open problems" in verdict["summary"]
    assert world[SHOPIFY] in verdict["evidence"]
    assert world[SALARY] not in verdict["evidence"]


def test_a_profile_denies_what_it_does_not_allow(client, world):
    sales = check(client, world["sales"], action="issue_credit", request={"amount": 20})
    assert sales["decision"] == "deny"
    assert sales["reasons"][0]["rule"] == "profile_action_not_allowed"
    support = check(client, world["support"], action="offer_discount", request={"amount": 20})
    assert support["decision"] == "deny"
    assert any(reason["rule"] == "profile_denied_action" for reason in support["reasons"])


def test_the_contact_preference_is_honoured(client, world):
    verdict = check(client, world["plain"], action="contact_customer", request={"channel": "email"})
    assert verdict["decision"] == "deny"
    assert verdict["summary"] == "The customer prefers whatsapp, not email."
    ok = check(client, world["plain"], action="contact_customer", request={"channel": "WhatsApp"})
    assert ok["decision"] == "allow"


def test_a_project_rule_denies_and_denial_outranks_approval(client, world):
    big = check(client, world["plain"], action="process_refund", request={"amount": 800})
    assert big["decision"] == "deny"
    assert big["summary"] == "Refunds over 500 go to finance."
    assert big["approval"] is None
    small = check(client, world["plain"], action="process_refund", request={"amount": 100})
    assert small["decision"] == "require_approval"
    assert small["approval"]["status"] == "pending"


def test_invalid_guardrails_are_refused_when_saved(client, world):
    response = client.put(
        f"{world['base']}/settings",
        json={"settings": {"guardrails": {"rules": [{"name": "typo", "when": "helth.score < 50"}]}}},
        headers=world["auth"],
    )
    assert response.status_code == 422
    assert "health.score" in response.text


def test_a_dry_run_records_nothing(client, world):
    before = client.get("/v1/agent/checks", headers=h(world["plain"])).json()["total"]
    verdict = check(client, world["plain"], action="issue_credit", request={"amount": 9}, dry_run=True)
    assert verdict["id"] == "dry_run" and verdict["decision"] == "require_approval"
    assert verdict["approval"] is None
    assert client.get("/v1/agent/checks", headers=h(world["plain"])).json()["total"] == before


def test_the_dashboard_simulates_a_profile_without_recording(client, world):
    before = client.get(f"{world['base']}/agent/checks", headers=world["auth"]).json()["total"]
    simulated = client.post(
        f"{world['base']}/agent/simulate",
        json={"customer_id": "acme", "action": "offer_discount", "profile_id": world["support_profile"]},
        headers=world["auth"],
    )
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["decision"] == "deny"
    assert simulated.json()["profile"] == "support-agent"
    assert client.get(f"{world['base']}/agent/checks", headers=world["auth"]).json()["total"] == before


# ------------------------------------------------------------------- approvals


def test_an_approval_is_filed_once_decided_by_a_person_and_redeemed_once(client, world):
    first = check(client, world["plain"], action="issue_credit", request={"amount": 50, "topic": "late delivery"})
    assert first["decision"] == "require_approval"
    approval = first["approval"]
    assert approval["status"] == "pending"
    # A retrying agent gets the same request back, not a second one.
    again = check(client, world["plain"], action="issue_credit", request={"amount": 50, "topic": "late delivery"})
    assert again["approval"]["id"] == approval["id"]

    waiting = check(
        client, world["plain"], action="issue_credit", request={"amount": 50, "topic": "late delivery"},
        approval_id=approval["id"],
    )
    assert waiting["decision"] == "require_approval"
    assert waiting["reasons"][0]["rule"] == "approval_pending"

    decided = client.post(
        f"{world['base']}/agent/approvals/{approval['id']}/decision",
        json={"decision": "approve", "note": "Goodwill credit is fine."},
        headers=world["auth"],
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"

    wrong = client.post(
        "/v1/agent/check",
        json={"customer_id": "acme", "action": "issue_credit", "request": {"amount": 5000, "topic": "late delivery"}, "approval_id": approval["id"]},
        headers=h(world["plain"]),
    )
    assert wrong.status_code == 422

    redeemed = check(
        client, world["plain"], action="issue_credit", request={"amount": 50, "topic": "late delivery"},
        approval_id=approval["id"],
    )
    assert redeemed["decision"] == "allow"
    assert redeemed["approval"]["status"] == "used"
    assert "Goodwill credit is fine" in redeemed["summary"]

    reused = check(
        client, world["plain"], action="issue_credit", request={"amount": 50, "topic": "late delivery"},
        approval_id=approval["id"],
    )
    assert reused["decision"] == "require_approval"
    assert reused["approval"]["id"] != approval["id"]
    assert reused["reasons"][0]["rule"] == "approval_used"

    again = client.post(
        f"{world['base']}/agent/approvals/{approval['id']}/decision",
        json={"decision": "reject"},
        headers=world["auth"],
    )
    assert again.status_code == 409


def test_a_rejection_is_a_denial(client, world):
    filed = check(client, world["plain"], action="waive_fee", request={"amount": 30})["approval"]
    rejected = client.post(
        f"/v1/agent/approvals/{filed['id']}/decision",
        json={"decision": "reject", "note": "Not this quarter."},
        headers=h(world["approver"]),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["decided_by"].startswith("api_key:")
    verdict = check(client, world["plain"], action="waive_fee", request={"amount": 30}, approval_id=filed["id"])
    assert verdict["decision"] == "deny"
    assert verdict["summary"] == "A person rejected this request: Not this quarter."


def test_who_may_decide(client, world):
    filed = check(client, world["plain"], action="issue_credit", request={"amount": 12})["approval"]
    path = f"/v1/agent/approvals/{filed['id']}/decision"
    body = {"decision": "approve"}
    # admin does not confer approvals:decide, and neither does the original project key.
    assert client.post(path, json=body, headers=h(world["admin_only"])).status_code == 403
    assert client.post(path, json=body, headers=h(world["original"])).status_code == 403
    # No key decides what it asked for itself.
    own = check(client, world["approver"], action="issue_credit", request={"amount": 13})["approval"]
    refused = client.post(f"/v1/agent/approvals/{own['id']}/decision", json=body, headers=h(world["approver"]))
    assert refused.status_code == 403
    assert client.post(path, json=body, headers=h(world["approver"])).status_code == 200


def test_an_approval_nobody_answered_lapses(client, world):
    filed = check(client, world["plain"], action="pause_subscription", request={"months": 1})["approval"]
    _sql("UPDATE agent_approvals SET expires_at = now() - interval '1 hour' WHERE id = :id", id=filed["id"])
    shown = client.get(f"/v1/agent/approvals/{filed['id']}", headers=h(world["plain"])).json()
    assert shown["status"] == "expired"
    verdict = check(client, world["plain"], action="pause_subscription", request={"months": 1}, approval_id=filed["id"])
    assert verdict["decision"] == "require_approval"
    assert verdict["reasons"][0]["rule"] == "approval_expired"
    assert verdict["approval"]["id"] != filed["id"]


def test_the_queue_and_the_webhook_hear_about_requests(client, world):
    endpoint = client.post(
        f"{world['base']}/webhooks",
        json={"url": "https://hooks.example.com/memora", "event_types": ["agent.approval_requested"]},
        headers=world["auth"],
    )
    assert endpoint.status_code == 201, endpoint.text
    check(client, world["plain"], action="downgrade_plan", request={"plan": "starter"})
    deliveries = client.get(f"{world['base']}/webhooks/deliveries", headers=world["auth"]).json()["data"]
    assert any(item["event_type"] == "agent.approval_requested" for item in deliveries)
    pending = client.get(f"{world['base']}/agent/approvals", params={"status": "pending"}, headers=world["auth"]).json()
    assert any(item["action"] == "downgrade_plan" for item in pending["data"])
    events = {item["event"] for item in client.get(f"{world['base']}/webhooks/events", headers=world["auth"]).json()}
    assert {"agent.approval_requested", "agent.approval_decided", "goal.achieved", "customer.state_changed"} <= events


def test_the_activity_header_counts_what_happened(client, world):
    activity = client.get(f"{world['base']}/agent/activity", headers=world["auth"]).json()
    assert activity["checks"].get("deny", 0) >= 1
    assert activity["checks"].get("require_approval", 0) >= 1
    assert any(item["rule"] == "open_problem_blocks_selling" for item in activity["top_rules"])


# ------------------------------------------------------------------------ runs


def test_every_answer_is_a_run_that_can_be_explained(client, world):
    answered = client.post(
        "/v1/memory/query",
        json={"customer_id": "acme", "query": "Is the Shopify sync still failing?"},
        headers=h(world["support"]),
    ).json()
    run_id = answered["run_id"]
    assert run_id

    runs = client.get("/v1/agent/runs", params={"agent": "support-agent"}, headers=h(world["support"])).json()
    assert run_id in {row["id"] for row in runs["data"]}

    explained = client.get(f"/v1/agent/runs/{run_id}/explain", headers=h(world["support"])).json()
    shopify = next(item for item in explained["memories"] if item["id"] == world[SHOPIFY])
    assert shopify["content_then"] == SHOPIFY
    assert shopify["strategies"]
    assert explained["held_back"]["profile"] == "support-agent"
    assert explained["held_back"]["withheld"] == 3
    assert explained["narrative"][0].startswith("support-agent asked")

    # Someone corrects the memory afterwards: the run still shows what the agent saw.
    corrected = client.post(
        f"/v1/memories/{world[SHOPIFY]}/feedback",
        json={"verdict": "correct", "content": "The Shopify sync was fixed on Monday."},
        headers=h(world["writer"]),
    )
    assert corrected.status_code == 200, corrected.text
    later = client.get(f"/v1/agent/runs/{run_id}/explain", headers=h(world["support"])).json()
    shopify = next(item for item in later["memories"] if item["id"] == world[SHOPIFY])
    assert shopify["content_then"] == SHOPIFY
    assert shopify["changed_since"]
    assert any("after this run" in line for line in later["narrative"])


def test_a_context_build_is_a_run_too(client, world):
    built = client.post(
        "/v1/memory/context", json={"customer_id": "acme", "task": "renewal call"}, headers=h(world["sales"])
    ).json()
    run = client.get(f"/v1/agent/runs/{built['run_id']}", headers=h(world["sales"])).json()
    assert run["kind"] == "context"
    assert run["agent"] == "sales-agent"
    assert run["trace"]["memories"]


def test_a_run_answer_is_withheld_from_a_less_cleared_reader(client, world):
    answered = client.post(
        "/v1/memory/query",
        json={"customer_id": "acme", "query": "What is broken with the salary export?"},
        headers=h(world["writer"]),
    ).json()
    assert world[SALARY] in {item["id"] for item in answered["memories"]}
    shown = client.get(f"/v1/agent/runs/{answered['run_id']}", headers=h(world["plain"])).json()
    assert shown["answer"] == "[withheld]"
    explained = client.get(f"/v1/agent/runs/{answered['run_id']}/explain", headers=h(world["plain"])).json()
    salary = next(item for item in explained["memories"] if item["id"] == world[SALARY])
    assert salary["visible"] is False and salary["content_then"] == "[withheld]"


def test_an_unbound_key_can_label_its_runs(client, world):
    answered = client.post(
        "/v1/memory/query",
        json={"customer_id": "acme", "query": "How do they want to be contacted?"},
        headers={**h(world["plain"]), "X-Agent-Name": "billing-bot"},
    ).json()
    run = client.get(f"/v1/agent/runs/{answered['run_id']}", headers=h(world["plain"])).json()
    assert run["agent"] == "billing-bot"


# ------------------------------------------------------ derived views elsewhere


def test_eval_suggestions_do_not_show_what_the_reader_may_not_see(client, world):
    suggestions = client.get("/v1/evals/suggestions", headers=h(world["plain"])).json()
    salary = [item for item in suggestions if "salary" in item["question"].lower()]
    assert salary, suggestions
    assert all(world[SALARY] not in {m["id"] for m in item["retrieved"]} for item in salary)
    assert all(item["answer"] == "[withheld]" for item in salary)


def test_raw_events_behind_a_hidden_memory_are_withheld(client, world):
    client.post("/v1/customers", json={"external_id": "initech"}, headers=h(world["writer"]))
    accepted = client.post(
        "/v1/events",
        json={"customer_id": "initech", "event_type": "support_message", "data": {"message": "Our salary run failed again this month."}},
        headers=h(world["writer"]),
    )
    assert accepted.status_code in (200, 202), accepted.text
    _process_pending(world["project_id"])

    event_id = accepted.json()["event_id"]
    writer_view = client.get(f"/v1/events/{event_id}", headers=h(world["writer"])).json()
    assert writer_view["withheld"] is False and writer_view["data"]
    plain_view = client.get(f"/v1/events/{event_id}", headers=h(world["plain"])).json()
    assert plain_view["withheld"] is True and plain_view["data"] == {}

    timeline = client.get("/v1/customers/initech/timeline", headers=h(world["plain"])).json()
    entry = next(item for item in timeline["entries"] if item["id"] == event_id)
    assert entry["detail"] is None and entry["metadata"]["withheld"] is True


def _process_pending(project_id: str) -> None:
    import anyio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database.models import Event
    from database.repositories import ProjectRepository
    from memory_engine import MemoryEngine
    from nlp import LocalEmbedder

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                project = await ProjectRepository(session).get(project_id)
                pending = (
                    await session.execute(
                        select(Event).where(Event.project_id == project_id, Event.status == "pending")
                    )
                ).scalars()
                memory_engine = MemoryEngine(session=session, embedder=LocalEmbedder())
                for event in list(pending):
                    await memory_engine.process_event(event=event, project=project)
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)


def test_a_hidden_preference_is_honoured_without_being_quoted(client, world):
    client.post("/v1/customers", json={"external_id": "hooli"}, headers=h(world["writer"]))
    client.post(
        "/v1/memories",
        json={"customer_id": "hooli", "content": "For salary questions they prefer WhatsApp.", "type": "preference"},
        headers=h(world["writer"]),
    )
    cleared = client.post(
        "/v1/agent/check",
        json={"customer_id": "hooli", "action": "contact_customer", "request": {"channel": "email"}},
        headers=h(world["writer"]),
    ).json()
    assert cleared["summary"] == "The customer prefers whatsapp, not email."
    hidden = client.post(
        "/v1/agent/check",
        json={"customer_id": "hooli", "action": "contact_customer", "request": {"channel": "email"}},
        headers=h(world["plain"]),
    ).json()
    # Still refused — the rule judged everything — but the words stay behind.
    assert hidden["decision"] == "deny"
    assert hidden["summary"] == "The customer's contact preference does not allow email."
    assert hidden["evidence"] == []
    # A stored check read later by the same reader is shaped the same way.
    stored = client.get(f"/v1/agent/checks/{hidden['id']}", headers=h(world["plain"])).json()
    assert stored["summary"] == hidden["summary"]
    stored_by_writer = client.get(f"/v1/agent/checks/{hidden['id']}", headers=h(world["writer"])).json()
    assert stored_by_writer["summary"] == "The customer prefers whatsapp, not email."
