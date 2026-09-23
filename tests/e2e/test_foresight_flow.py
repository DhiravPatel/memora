"""The four forward-looking features, over HTTP, against a real database.

Predictive signals, next-best-action recommendations, goal tracking and cross-session
agent memory — driven exactly as a customer's backend and their agent would drive them.
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
def account(client: TestClient) -> dict[str, str]:
    unique = str(int(time.time() * 1000))
    signup = client.post(
        "/v1/auth/signup",
        json={
            "email": f"foresight-{unique}@example.com",
            "password": "a-strong-password",
            "name": "Owner",
            "organization_name": f"Foresight {unique}",
        },
    )
    assert signup.status_code == 201, signup.text
    token = signup.json()["tokens"]["access_token"]
    project = client.post(
        "/v1/projects", json={"name": "Production"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert project.status_code == 201, project.text
    body = project.json()
    return {
        "auth_header": f"Bearer {token}",
        "project_id": body["id"],
        "api_key": body["api_key"],
    }


def api(account: dict[str, str]) -> dict[str, str]:
    return {"X-API-Key": account["api_key"]}


def user(account: dict[str, str]) -> dict[str, str]:
    return {"Authorization": account["auth_header"]}


def ingest(client: TestClient, account: dict, customer: str, event_type: str, data: dict) -> None:
    response = client.post(
        "/v1/events",
        json={"customer_id": customer, "event_type": event_type, "data": data},
        headers=api(account),
    )
    assert response.status_code in (200, 202), response.text


def drive_pipeline(client: TestClient, account: dict) -> None:
    """Run the worker's full post-processing inline, the way ``process_event`` does.

    The API's engine lives in the TestClient's event loop, so this opens its own engine in
    its own loop rather than borrowing a connection across loops.
    """
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from common.settings import get_settings as _get_settings
    from database.models import Event
    from database.repositories import CustomerRepository, ProjectRepository
    from memory_engine import MemoryEngine
    from nlp import LocalEmbedder

    pending = client.get(
        "/v1/events", params={"status": "pending", "limit": 200}, headers=api(account)
    ).json()["data"]
    if not pending:
        return

    async def run() -> None:
        engine = create_async_engine(_get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                memory_engine = MemoryEngine(session=session, embedder=LocalEmbedder())
                projects = ProjectRepository(session)
                customers = CustomerRepository(session)
                touched: dict[str, tuple] = {}
                for item in pending:
                    event = await session.get(Event, item["id"])
                    project = await projects.get(item["project_id"])
                    if event is None or project is None:
                        continue
                    await memory_engine.process_event(event=event, project=project)
                    customer = await customers.get(event.customer_id, project.id)
                    if customer is not None:
                        touched[customer.id] = (project, customer)

                for project, customer in touched.values():
                    await memory_engine.refresh_health(project=project, customer=customer)
                    await memory_engine.refresh_goals(project=project, customer=customer)
                    report = await memory_engine.signals_for(project=project, customer=customer)
                    await memory_engine.record_signals(
                        project=project, customer=customer, report=report
                    )
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)


@pytest.fixture(scope="module")
def declining_customer(client: TestClient, account: dict) -> str:
    """A customer whose story is a slow-motion churn."""
    customer = "cus_declining"
    story = [
        ("support_message", {"message": "The Shopify sync keeps failing for us."}),
        ("support_message", {"message": "The Shopify sync is still failing, this is urgent."}),
        ("support_message", {"message": "Shopify sync broken again, third time this month."}),
        ("support_message", {"message": "We are evaluating a competitor because of this."}),
        ("support_message", {"message": "We want to migrate our whole catalogue to your platform."}),
    ]
    for event_type, data in story:
        ingest(client, account, customer, event_type, data)
    drive_pipeline(client, account)
    return customer


# ------------------------------------------------------------------- signals


def test_signals_describe_a_trajectory_with_evidence(client, account, declining_customer):
    response = client.get(f"/v1/customers/{declining_customer}/signals", headers=api(account))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["trajectory"] in ("improving", "steady", "declining")
    assert 0 <= body["churn_risk"] <= 1
    assert body["headline"]
    assert body["signals"], "a customer with this history should produce signals"

    for signal in body["signals"]:
        assert signal["rationale"].strip()
        assert signal["direction"] in ("risk", "opportunity")
        assert 0 < signal["strength"] <= 1

    risks = {signal["key"] for signal in body["signals"] if signal["direction"] == "risk"}
    assert "churn_language" in risks or "escalating_problems" in risks


def test_the_daily_snapshot_becomes_the_series(client, account, declining_customer):
    body = client.get(
        f"/v1/customers/{declining_customer}/signals", headers=api(account)
    ).json()
    assert len(body["series"]) >= 1
    point = body["series"][-1]
    assert 0 <= point["churn_risk"] <= 1
    assert point["trajectory"] in ("improving", "steady", "declining")


def test_signals_for_an_unknown_customer_are_a_404(client, account):
    response = client.get("/v1/customers/cus_nope/signals", headers=api(account))
    assert response.status_code == 404


def test_the_portfolio_view_ranks_declining_customers_first(client, account, declining_customer):
    response = client.get(
        f"/v1/projects/{account['project_id']}/signals", headers=user(account)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["customers"]
    assert set(body["trajectories"]) == {"improving", "steady", "declining"}

    order = {"declining": 0, "steady": 1, "improving": 2}
    positions = [order[item["trajectory"]] for item in body["customers"]]
    assert positions == sorted(positions)


# ----------------------------------------------------------- recommendations


def test_recommendations_are_actionable_and_evidenced(client, account, declining_customer):
    response = client.get(
        f"/v1/customers/{declining_customer}/recommendations", headers=api(account)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["recommendations"], "a declining customer should have something to do"
    assert body["summary"]
    urgencies = [item["urgency"] for item in body["recommendations"]]
    assert urgencies == sorted(urgencies, reverse=True)

    for item in body["recommendations"]:
        assert item["priority"] in ("now", "soon", "when_you_can")
        assert item["rationale"].strip()
        assert item["playbook"]
        assert item["memory_ids"] or item["goal_ids"] or item["signals"]


def test_recommendations_reference_real_memories(client, account, declining_customer):
    body = client.get(
        f"/v1/customers/{declining_customer}/recommendations", headers=api(account)
    ).json()
    cited = {mid for item in body["recommendations"] for mid in item["memory_ids"]}
    if not cited:
        pytest.skip("no memory-backed recommendation in this fixture")

    memories = client.get(
        f"/v1/customers/{declining_customer}/memories",
        params={"limit": 200},
        headers=api(account),
    ).json()
    known = {memory["id"] for memory in memories["data"]}
    assert cited <= known


# ---------------------------------------------------------------------- goals


def test_a_stated_goal_is_tracked_then_closed_by_later_evidence(client, account):
    customer = "cus_goals"
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "We want to roll out SSO to our whole sales team this quarter."},
    )
    drive_pipeline(client, account)

    goals = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()
    assert goals["total"] == 1, goals
    goal = goals["data"][0]
    assert goal["status"] == "open"
    assert "sso" in [keyword.lower() for keyword in goal["keywords"]]
    assert goal["evidence"][0]["kind"] == "stated"

    # A later message that never mentions the goal should still close it.
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "SSO is now live for the whole sales team, thanks for the help."},
    )
    drive_pipeline(client, account)

    goal = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()["data"][0]
    assert goal["status"] == "achieved"
    assert goal["progress"] == 1.0
    assert goal["closed_at"]
    assert any(entry["kind"] == "achieved" for entry in goal["evidence"])


def test_a_goal_is_not_opened_twice_for_a_restatement(client, account):
    customer = "cus_goals"
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "We still want SSO rolled out to the sales team."},
    )
    drive_pipeline(client, account)
    goals = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()
    assert goals["total"] == 1


def test_a_person_can_override_a_goal_and_the_tracker_backs_off(client, account):
    customer = "cus_override"
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "We plan to migrate our billing data across next month."},
    )
    drive_pipeline(client, account)

    goal = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()["data"][0]
    assert goal["overridden"] is False

    patched = client.patch(
        f"/v1/goals/{goal['id']}",
        json={"status": "abandoned", "note": "They told us on a call."},
        headers=api(account),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "abandoned"
    assert patched.json()["overridden"] is True

    # Evidence that would otherwise have moved it must now be ignored.
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "The billing data migration is complete."},
    )
    drive_pipeline(client, account)
    after = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()["data"][0]
    assert after["status"] == "abandoned"


def test_goal_overrides_are_audited(client, account):
    audit = client.get(
        f"/v1/projects/{account['project_id']}/audit-logs",
        params={"action": "goal_change", "limit": 20},
        headers=user(account),
    )
    assert audit.status_code == 200, audit.text
    entries = audit.json()
    assert any(entry["resource_type"] == "goal" for entry in entries), entries


def test_the_project_goal_list_and_summary_agree(client, account):
    listed = client.get("/v1/goals", params={"limit": 100}, headers=api(account)).json()
    summary = client.get("/v1/goals/summary", headers=api(account)).json()
    assert summary["total"] == listed["total"]
    assert summary["achieved"] >= 1
    assert summary["summary"]


# -------------------------------------------------------- agent sessions


def test_an_agent_session_carries_memory_in_and_out(client, account, declining_customer):
    opened = client.post(
        "/v1/agent/sessions",
        json={"customer_id": declining_customer, "agent": "support-bot", "external_id": "conv-1"},
        headers=api(account),
    )
    assert opened.status_code == 201, opened.text
    session = opened.json()
    assert session["status"] == "open"
    assert session["resumed"] is False
    assert session["context"]["text"], "an agent must be briefed on the way in"
    assert session["context"]["prior_sessions"] == []

    turn = client.post(
        f"/v1/agent/sessions/{session['id']}/turns",
        json={"role": "user", "content": "Is the Shopify sync fixed yet?"},
        headers=api(account),
    )
    assert turn.status_code == 201, turn.text
    body = turn.json()
    assert body["turn"]["role"] == "user"
    assert body["event_id"], "a customer turn should become an event"
    assert body["answer"] is not None
    assert body["context"]["text"]

    reply = client.post(
        f"/v1/agent/sessions/{session['id']}/turns",
        json={"role": "agent", "content": "Not yet — engineering is on it."},
        headers=api(account),
    )
    assert reply.status_code == 201
    # The agent's own words are recorded but never learned from.
    assert reply.json()["event_id"] is None

    closed = client.post(
        f"/v1/agent/sessions/{session['id']}/close",
        json={"write_summary": True, "outcome": "Escalated to engineering"},
        headers=api(account),
    )
    assert closed.status_code == 200, closed.text
    payload = closed.json()
    assert payload["summary"], "closing a session must leave something behind"
    assert payload["summary_memory_id"]
    assert "Escalated to engineering" in payload["summary"]
    assert payload["session"]["status"] == "closed"


def test_the_next_session_starts_where_the_last_one_finished(client, account, declining_customer):
    opened = client.post(
        "/v1/agent/sessions",
        json={"customer_id": declining_customer, "agent": "support-bot", "external_id": "conv-2"},
        headers=api(account),
    )
    assert opened.status_code == 201, opened.text
    prior = opened.json()["context"]["prior_sessions"]
    assert prior, "the previous conversation should be handed to this one"
    assert prior[0]["summary"]
    assert prior[0]["turn_count"] >= 1


def test_the_session_summary_is_a_memory_like_any_other(client, account, declining_customer):
    memories = client.get(
        f"/v1/customers/{declining_customer}/memories",
        params={"type": "summary", "limit": 50},
        headers=api(account),
    ).json()
    summaries = [memory for memory in memories["data"] if memory["source"] == "agent"]
    assert summaries, "the closed session should have written a summary memory"
    assert "the customer said" in summaries[0]["content"].lower()


def test_opening_the_same_conversation_twice_resumes_it(client, account, declining_customer):
    first = client.post(
        "/v1/agent/sessions",
        json={"customer_id": declining_customer, "external_id": "conv-resume"},
        headers=api(account),
    ).json()
    second = client.post(
        "/v1/agent/sessions",
        json={"customer_id": declining_customer, "external_id": "conv-resume"},
        headers=api(account),
    )
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    assert second.json()["resumed"] is True


def test_a_closed_session_refuses_further_turns(client, account, declining_customer):
    opened = client.post(
        "/v1/agent/sessions",
        json={"customer_id": declining_customer, "external_id": "conv-closed"},
        headers=api(account),
    ).json()
    client.post(
        f"/v1/agent/sessions/{opened['id']}/close",
        json={"write_summary": False},
        headers=api(account),
    )
    rejected = client.post(
        f"/v1/agent/sessions/{opened['id']}/turns",
        json={"role": "user", "content": "one more thing"},
        headers=api(account),
    )
    assert rejected.status_code == 409, rejected.text


def test_a_session_for_an_unknown_customer_is_a_404(client, account):
    response = client.post(
        "/v1/agent/sessions", json={"customer_id": "cus_missing"}, headers=api(account)
    )
    assert response.status_code == 404


def test_the_transcript_is_readable_afterwards(client, account, declining_customer):
    sessions = client.get(
        "/v1/agent/sessions",
        params={"customer_id": declining_customer, "limit": 10},
        headers=api(account),
    ).json()
    assert sessions["total"] >= 1

    detail = client.get(
        f"/v1/agent/sessions/{sessions['data'][0]['id']}", headers=api(account)
    ).json()
    assert isinstance(detail["turns"], list)


def test_opening_a_session_needs_a_write_scope(client, account, declining_customer):
    """A read-only agent key can be briefed but must not be able to write memory."""
    created = client.post(
        f"/v1/projects/{account['project_id']}/api-keys",
        json={"name": "read-only", "scopes": ["memory:read", "customers:read"]},
        headers=user(account),
    )
    assert created.status_code == 201, created.text
    read_only = {"X-API-Key": created.json()["api_key"]}

    denied = client.post(
        "/v1/agent/sessions", json={"customer_id": declining_customer}, headers=read_only
    )
    assert denied.status_code == 403, denied.text

    allowed = client.get("/v1/agent/sessions", headers=read_only)
    assert allowed.status_code == 200


# ------------------------------------------------------------------ dashboard


def test_the_dashboard_sees_the_same_numbers_as_the_api(client, account, declining_customer):
    project_id = account["project_id"]
    via_key = client.get(
        f"/v1/customers/{declining_customer}/signals", headers=api(account)
    ).json()
    via_session = client.get(
        f"/v1/projects/{project_id}/customers/{declining_customer}/signals", headers=user(account)
    )
    assert via_session.status_code == 200, via_session.text
    assert via_session.json()["trajectory"] == via_key["trajectory"]
    assert via_session.json()["churn_risk"] == via_key["churn_risk"]


def test_the_export_contains_the_new_surfaces(client, account, declining_customer):
    bundle = client.get(
        f"/v1/customers/{declining_customer}/export", headers=api(account)
    ).json()
    assert bundle["export_version"] == "1.1"
    assert "goals" in bundle and "signals" in bundle and "agent_sessions" in bundle
    assert bundle["signals"]["trajectory"] in ("improving", "steady", "declining")
    assert bundle["counts"]["agent_sessions"] >= 1


def test_deleting_a_customer_takes_their_goals_and_sessions(client, account):
    customer = "cus_forget_me"
    ingest(
        client,
        account,
        customer,
        "support_message",
        {"message": "We want to connect our Salesforce instance this month."},
    )
    drive_pipeline(client, account)
    client.post(
        "/v1/agent/sessions", json={"customer_id": customer, "external_id": "conv-doomed"},
        headers=api(account),
    )

    goals_before = client.get(f"/v1/customers/{customer}/goals", headers=api(account)).json()
    assert goals_before["total"] >= 1

    deleted = client.delete(f"/v1/customers/{customer}", headers=api(account))
    assert deleted.status_code == 200, deleted.text
    removed = deleted.json()["removed"]
    assert removed["goals"] >= 1
    assert removed["agent_sessions"] >= 1

    assert client.get(f"/v1/customers/{customer}/goals", headers=api(account)).status_code == 404


def test_finished_conversations_age_out_but_their_memory_does_not(client, account):
    """Raw transcripts are customer text; the summary they produced is memory."""
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.retention_service import RetentionService
    from common.settings import get_settings as _get_settings
    from database.models import AgentSession
    from database.repositories import ProjectRepository

    customer = "cus_retention"
    ingest(client, account, customer, "support_message", {"message": "The importer is broken."})
    drive_pipeline(client, account)

    opened = client.post(
        "/v1/agent/sessions",
        json={"customer_id": customer, "external_id": "conv-retention"},
        headers=api(account),
    ).json()
    client.post(
        f"/v1/agent/sessions/{opened['id']}/turns",
        json={"role": "user", "content": "The importer is still broken for us."},
        headers=api(account),
    )
    closed = client.post(
        f"/v1/agent/sessions/{opened['id']}/close", json={}, headers=api(account)
    ).json()
    summary_memory_id = closed["summary_memory_id"]
    assert summary_memory_id

    async def age_and_sweep() -> int:
        engine = create_async_engine(_get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                from datetime import timedelta

                from common.time import utcnow

                stored = await session.get(AgentSession, opened["id"])
                stored.last_active_at = utcnow() - timedelta(days=400)
                await session.flush()
                project = await ProjectRepository(session).get(account["project_id"])
                outcome = await RetentionService(session).apply(project)
                await session.commit()
                return outcome.agent_sessions_deleted
        finally:
            await engine.dispose()

    assert anyio.run(age_and_sweep) >= 1

    gone = client.get(f"/v1/agent/sessions/{opened['id']}", headers=api(account))
    assert gone.status_code == 404

    # What the conversation established is still there.
    kept = client.get(f"/v1/memories/{summary_memory_id}", headers=api(account))
    assert kept.status_code == 200, kept.text
    assert kept.json()["source"] == "agent"


def test_health_weights_are_per_project(client, account):
    """Two projects, the same story, different definitions of "unhealthy"."""
    customer = "cus_weights"
    for message in (
        {"message": "The CSV export is broken for us."},
        {"message": "The importer is failing too."},
    ):
        ingest(client, account, customer, "support_message", message)
    drive_pipeline(client, account)

    before = client.get(f"/v1/customers/{customer}/health", headers=api(account)).json()

    # Make open problems hurt twice as much.
    updated = client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={"settings": {"health_weights": {"open_problem": -14.0}}},
        headers=user(account),
    )
    assert updated.status_code == 200, updated.text

    after = client.get(f"/v1/customers/{customer}/health", headers=api(account)).json()
    assert after["score"] < before["score"]
    assert after["factors"], "the factors should still explain the score"

    # The portfolio view reads the same weights, so the two cannot disagree.
    portfolio = client.get(
        f"/v1/projects/{account['project_id']}/health",
        params={"bands": "healthy,watch,at_risk,critical", "limit": 100},
        headers=user(account),
    ).json()
    listed = next(
        item for item in portfolio["customers"] if item["customer_id"] == after["customer_id"]
    )
    assert listed["score"] == after["score"]

    # Put it back so later tests see the default scoring.
    client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={"settings": {"health_weights": {"open_problem": -7.0}}},
        headers=user(account),
    )


def test_an_out_of_range_health_weight_is_refused(client, account):
    refused = client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={"settings": {"health_weights": {"open_problem": -500}}},
        headers=user(account),
    )
    assert refused.status_code == 422, refused.text
    assert "at least" in refused.text


def test_an_unknown_health_factor_is_refused(client, account):
    refused = client.put(
        f"/v1/projects/{account['project_id']}/settings",
        json={"settings": {"health_weights": {"vibes": -5}}},
        headers=user(account),
    )
    assert refused.status_code == 422, refused.text
    assert "unknown keys" in refused.text


# ------------------------------------------------------------------ vocabulary


def test_a_curated_glossary_entry_survives_mining(client, account):
    """A person's word outranks the corpus, and mining must not undo it."""
    project_id = account["project_id"]

    created = client.post(
        f"/v1/projects/{project_id}/vocabulary",
        json={"term": "loader", "synonym": "importer", "note": "What support calls it."},
        headers=user(account),
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["source"] == "curated"
    assert entry["score"] == 1.0
    assert entry["support"] == 0

    # Mining rewrites the mined table; the curated row has to still be there afterwards.
    _mine(account)

    listed = client.get(
        f"/v1/projects/{project_id}/vocabulary", params={"limit": 400}, headers=user(account)
    ).json()
    pairs = {(item["term"], item["synonym"]): item for item in listed["terms"]}
    assert ("loader", "importer") in pairs
    assert pairs[("loader", "importer")]["source"] == "curated"
    assert listed["curated"] >= 1


@pytest.fixture(scope="module")
def mined_corpus(client: TestClient, account: dict) -> str:
    """Enough memories, with real co-occurrence, for the miner to find something.

    Written straight in as memories rather than as events: this is a test about the
    vocabulary table, and the extraction pipeline is exercised everywhere else.
    """
    customer = "cus_vocab"
    client.post(
        "/v1/customers", json={"external_id": customer, "name": "Vocab"}, headers=api(account)
    )
    sentences = [
        "the nightly ledger reconciliation failed again",
        "ledger reconciliation is still failing for us",
        "our ledger reconciliation has not run since Tuesday",
        "ledger reconciliation finally completed overnight",
        "the warehouse dispatch queue is backed up",
        "warehouse dispatch is slow again this morning",
        "warehouse dispatch queue cleared after a restart",
        "nothing to do with any of the above",
    ]
    for index in range(28):
        client.post(
            "/v1/memories",
            json={
                "customer_id": customer,
                "content": sentences[index % len(sentences)] + f" (report {index})",
                "type": "problem",
            },
            headers=api(account),
        )
    return customer


def test_a_rejected_pair_is_not_used_and_not_relearned(client, account, mined_corpus):
    project_id = account["project_id"]
    _mine(account)

    listed = client.get(
        f"/v1/projects/{project_id}/vocabulary", params={"limit": 400}, headers=user(account)
    ).json()
    mined = [item for item in listed["terms"] if item["source"] == "mined"]
    assert mined, "the seeded corpus should have produced mined pairs"

    victim = mined[0]
    rejected = client.delete(
        f"/v1/projects/{project_id}/vocabulary/{victim['term']}/{victim['synonym']}",
        headers=user(account),
    )
    assert rejected.status_code == 200, rejected.text

    active = client.get(
        f"/v1/projects/{project_id}/vocabulary", params={"limit": 400}, headers=user(account)
    ).json()
    assert (victim["term"], victim["synonym"]) not in {
        (item["term"], item["synonym"]) for item in active["terms"]
    }

    # The decisive part: tonight's run must not put it back.
    _mine(account)
    after = client.get(
        f"/v1/projects/{project_id}/vocabulary", params={"limit": 400}, headers=user(account)
    ).json()
    assert (victim["term"], victim["synonym"]) not in {
        (item["term"], item["synonym"]) for item in after["terms"]
    }
    assert after["rejected"] >= 1

    # And it is visible under the rejected filter, not silently gone.
    shown = client.get(
        f"/v1/projects/{project_id}/vocabulary",
        params={"status": "rejected", "limit": 400},
        headers=user(account),
    ).json()
    assert (victim["term"], victim["synonym"]) in {
        (item["term"], item["synonym"]) for item in shown["terms"]
    }


def test_glossary_entries_are_validated(client, account):
    project_id = account["project_id"]
    same = client.post(
        f"/v1/projects/{project_id}/vocabulary",
        json={"term": "sync", "synonym": "sync"},
        headers=user(account),
    )
    assert same.status_code == 409, same.text

    short = client.post(
        f"/v1/projects/{project_id}/vocabulary",
        json={"term": "a", "synonym": "bb"},
        headers=user(account),
    )
    assert short.status_code == 422


def test_rejecting_something_that_is_not_there_is_a_404(client, account):
    denied = client.delete(
        f"/v1/projects/{account['project_id']}/vocabulary/nonsense/gibberish",
        headers=user(account),
    )
    assert denied.status_code == 404


def _mine(account: dict) -> None:
    """Run the mining logic inline, the way the nightly cron would.

    Its own engine and loop, because the worker's global engine belongs to the
    TestClient's.
    """
    import anyio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from common.settings import get_settings as _get_settings
    from worker.tasks.vocabulary import mine_project_vocabulary

    async def run() -> None:
        engine = create_async_engine(_get_settings().database_url, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await mine_project_vocabulary(session, account["project_id"])
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(run)
