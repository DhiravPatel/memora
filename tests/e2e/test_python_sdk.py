"""The Python SDK, driven against a real HTTP server.

The API runs on a real socket for these tests rather than an in-process transport, so the
SDK's own headers, retries, timeouts and error mapping are exercised exactly as they are
in production. A field rename in the API breaks this test, which is the point.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from ai_memory import MemoryAPIError, MemoryClient  # noqa: E402
from tests.conftest import database_required  # noqa: E402

from app.main import create_app  # noqa: E402
from common.settings import get_settings  # noqa: E402
from database import models  # noqa: F401,E402
from database.base import Base  # noqa: E402

pytestmark = [pytest.mark.e2e, database_required]

# The dashboard credentials behind the live server, for tests that need a second key.
LIVE: dict[str, str] = {}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def live_server():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)

    app = create_app()

    # Create the account through the app in-process; the SDK then talks over real HTTP.
    with TestClient(app) as client:
        unique = str(int(time.time() * 1000))
        signup = client.post(
            "/v1/auth/signup",
            json={
                "email": f"sdk-{unique}@example.com",
                "password": "a-strong-password",
                "organization_name": f"SDK {unique}",
            },
        )
        token = signup.json()["tokens"]["access_token"]
        project = client.post(
            "/v1/projects",
            json={"name": "SDK"},
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        LIVE.update(token=token, project_id=project["id"])

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the API server did not start"

    try:
        yield f"http://127.0.0.1:{port}", project["api_key"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        with engine.begin() as connection:
            Base.metadata.drop_all(connection)
        engine.dispose()


@pytest.fixture
def memory(live_server):
    base_url, api_key = live_server
    with MemoryClient(api_key=api_key, base_url=base_url, max_retries=1) as client:
        yield client


def test_track_and_read_back(memory):
    tracked = memory.track(
        customer_id="cus_sdk",
        type="support_message",
        data={"message": "Shopify sync has failed three times this week."},
        external_event_id="sdk-1",
        customer_name="Dana",
    )
    assert tracked.event_id.startswith("evt_")
    assert tracked.status == "accepted"

    customer = memory.get_customer("cus_sdk")
    assert customer.external_id == "cus_sdk"
    assert customer.name == "Dana"


def test_preview_explains_without_sending(memory):
    """A dry run through the SDK, over a real socket. Nothing may be stored."""
    before = len(memory.memories("cus_sdk", limit=100))

    preview = memory.preview(
        customer_id="cus_sdk",
        type="support_message",
        data={"message": "The nightly export has been silently dropping rows."},
    )

    assert preview.would_process is True
    assert preview.stop_reason is None
    assert preview.memories
    assert preview.memories[0].action
    assert preview.memories[0].reason
    # __bool__ is the ergonomic bit: `if not preview:` has to mean "nothing would happen".
    assert bool(preview) is True

    assert len(memory.memories("cus_sdk", limit=100)) == before, "a preview must not write"


def test_preview_reports_why_nothing_would_happen(memory):
    preview = memory.preview(customer_id="cus_sdk", type="page_view", data={"path": "/x"})

    assert bool(preview) is False
    assert preview.stop_reason
    assert f"{preview.threshold:.2f}" in preview.stop_reason


def test_idempotent_tracking(memory):
    first = memory.track(
        customer_id="cus_sdk", type="feature_used",
        data={"feature": "reports"}, external_event_id="sdk-dup",
    )
    second = memory.track(
        customer_id="cus_sdk", type="feature_used",
        data={"feature": "reports"}, external_event_id="sdk-dup",
    )
    assert first.event_id == second.event_id
    assert second.status in ("duplicate", "accepted")


def test_customer_360_through_the_sdk(memory):
    """One call, over a real socket, with the ergonomics the SDK adds on top."""
    view = memory.customer_360("cus_sdk")

    assert view.customer["external_id"] == "cus_sdk"
    assert view.summary
    assert view.health_score is not None
    assert isinstance(view.active_problems, list)
    # A section that was not requested reads as absent, not as empty.
    assert view.section("nonexistent") is None

    trimmed = memory.customer_360("cus_sdk", include=["health"])
    assert set(trimmed.sections) == {"health"}
    assert trimmed.health_score == view.health_score


def test_facts_conditions_and_lifecycle_through_the_sdk(memory):
    """Phase 1 of §26 over a real socket: facts, a rule, the lifecycle and snapshots."""
    memory.remember("cus_sdk", "The customer will cancel if the Shopify sync is not fixed.", type="problem")

    facts = memory.facts("cus_sdk")
    assert "cancellation" in facts["values"]["intents.kinds"]

    result = memory.evaluate_condition("cus_sdk", 'intents.kinds contains "cancellation"')
    assert result.matched and bool(result) is True
    assert result.evidence

    refreshed = memory.refresh_lifecycle_state("cus_sdk")
    assert refreshed["state"] == "at_risk"
    state = memory.lifecycle_state("cus_sdk")
    assert state is not None and state.state == "at_risk" and state.transition == "at_risk"

    pinned = memory.set_lifecycle_state("cus_sdk", "active", note="They are fine.")
    assert pinned.pinned and pinned.source == "manual"
    released = memory.release_lifecycle_state("cus_sdk")
    assert released.pinned is False

    assert memory.snapshots("cus_sdk"), "the refresh recorded what it saw"
    assert "at_risk" in memory.lifecycle()["counts"]


def test_manual_memory_query_and_context(memory):
    memory.remember(
        "cus_sdk",
        "The customer's Shopify integration fails during checkout.",
        type="problem",
        importance=0.9,
    )
    memory.remember("cus_sdk", "The customer prefers WhatsApp for support.", type="preference")

    memories = memory.memories("cus_sdk")
    assert any("Shopify" in item.content for item in memories)

    result = memory.query("cus_sdk", "What problems has this customer experienced?")
    assert result.has_answer
    assert result.memories

    context = memory.context("cus_sdk", task="support_response", as_text=True)
    assert context.prompt_text
    assert context.memory_ids
    assert context.active_problems


def test_health_and_feedback(memory):
    health = memory.health("cus_sdk")
    assert 0 <= health.score <= 100
    assert health.band in ("healthy", "watch", "at_risk", "critical")

    target = next(item for item in memory.memories("cus_sdk") if item.type == "problem")
    result = memory.feedback(target.id, "confirm", note="verified on a call")
    assert result["confidence"] >= target.confidence


async def test_async_client_shares_the_same_surface(live_server):
    base_url, api_key = live_server
    from ai_memory import AsyncMemoryClient

    async with AsyncMemoryClient(api_key=api_key, base_url=base_url, max_retries=0) as client:
        tracked = await client.track(
            customer_id="cus_async",
            type="support_message",
            data={"message": "The billing export is broken for us."},
            external_event_id="sdk-async-1",
        )
        assert tracked.status == "accepted"
        health = await client.health("cus_async")
        assert health.customer_id


def test_errors_are_typed(memory):
    with pytest.raises(MemoryAPIError) as error:
        memory.get_customer("cus_does_not_exist")
    assert error.value.status == 404
    assert error.value.code == "not_found"
    assert not error.value.is_retryable


def test_export_and_delete(memory):
    bundle = memory.export("cus_sdk")
    assert bundle["counts"]["memories"] >= 2

    deleted = memory.delete_customer("cus_sdk")
    assert deleted["deleted"] is True
    with pytest.raises(MemoryAPIError):
        memory.get_customer("cus_sdk")


def test_bulk_upsert_and_merge_through_the_sdk(memory):
    bulk = memory.upsert_customers(
        [
            {"external_id": "cus_sdk_a", "name": "Alpha"},
            {"external_id": "cus_sdk_b", "name": "Beta"},
        ]
    )
    assert bulk["created"] == 2

    memory.track(
        customer_id="cus_sdk_b",
        type="support_message",
        data={"message": "The export keeps failing for us."},
        external_event_id="sdk-merge-1",
    )
    merged = memory.merge_customers("cus_sdk_b", "cus_sdk_a")
    assert merged["events_moved"] >= 1
    assert memory.get_customer("cus_sdk_a").external_id == "cus_sdk_a"


def test_webhook_helpers_are_stdlib_only():
    """A receiver should be able to verify a payload without the HTTP client."""
    import subprocess
    import sys
    from pathlib import Path

    sdk_path = str(Path(__file__).resolve().parents[2] / "sdk" / "python")
    script = (
        f"import sys; sys.path.insert(0, {sdk_path!r});"
        "from ai_memory.webhooks import verify_signature, construct_event;"
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


@pytest.fixture
def foresight_customer(memory) -> str:
    """Own customer, because the export/delete test above removes ``cus_sdk``."""
    customer = "cus_sdk_foresight"
    memory.track(
        customer_id=customer,
        type="support_message",
        data={"message": "The Shopify sync keeps failing for us."},
        external_event_id="sdk-foresight-1",
    )
    memory.remember(
        customer,
        "The customer wants to roll out SSO to their whole sales team.",
        type="goal",
    )
    memory.remember(
        customer,
        "The customer is evaluating a competitor after repeated sync failures.",
        type="intent",
    )
    return customer


def test_foresight_surfaces_through_the_sdk(memory, foresight_customer):
    """Signals and recommendations, as a customer's backend would read them."""
    report = memory.signals(foresight_customer)
    assert report.trajectory in ("improving", "steady", "declining")
    assert 0 <= report.churn_risk <= 1
    assert report.headline
    # The convenience properties are part of the contract, not decoration.
    assert report.risks == [signal for signal in report.signals if signal.direction == "risk"]
    assert all(signal.rationale for signal in report.signals)

    for action in memory.recommendations(foresight_customer):
        assert action.priority in ("now", "soon", "when_you_can")
        assert action.action and action.rationale
        assert action.is_urgent == (action.priority == "now")


def test_agent_sessions_carry_memory_between_conversations(memory, foresight_customer):
    first = memory.open_session(foresight_customer, agent="support-bot", external_id="sdk-conv-1")
    assert first.is_open
    assert first.resumed is False
    assert first.prompt_text, "an agent must be briefed on the way in"

    resumed = memory.open_session(foresight_customer, external_id="sdk-conv-1")
    assert resumed.id == first.id
    assert resumed.resumed is True

    turn = memory.add_turn(first.id, "Has the Shopify sync been fixed?")
    assert turn.turn.role == "user"
    assert turn.event_id, "a customer turn should become an event"
    assert turn.turn_count >= 1

    closed = memory.close_session(first.id, outcome="Promised a fix by Friday")
    assert closed.status == "closed"
    assert closed.summary and "Promised a fix by Friday" in closed.summary

    # The next conversation is handed what this one established.
    second = memory.open_session(foresight_customer, external_id="sdk-conv-2")
    assert second.context is not None
    assert second.context.prior_sessions
    assert second.context.prior_sessions[0].summary
    assert "Earlier conversation" in second.prompt_text


def test_goal_status_can_be_corrected_through_the_sdk(memory, foresight_customer):
    goals = memory.goals(foresight_customer)
    assert goals, "the goal memory should have been tracked"
    goal = goals[0]
    assert goal.is_live

    corrected = memory.set_goal_status(goal.id, "achieved", note="Confirmed on a call")
    assert corrected.status == "achieved"
    assert corrected.overridden is True
    assert corrected.is_live is False


# ------------------------------------------------------------------ agents (§26 3)


def _dashboard_key(base_url: str, name: str, scopes: list[str], profile_id: str | None = None) -> str:
    import httpx

    response = httpx.post(
        f"{base_url}/v1/projects/{LIVE['project_id']}/api-keys",
        json={"name": name, "scopes": scopes, "agent_profile_id": profile_id},
        headers={"Authorization": f"Bearer {LIVE['token']}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["api_key"]


def test_guardrails_approvals_and_runs_through_the_sdk(memory, live_server):
    from ai_memory import ActionCheck, AgentRun, RunExplanation

    base_url, _ = live_server
    memory.upsert_customer("cus_agent")
    memory.remember("cus_agent", "Their invoice export fails every Monday.", type="problem")

    refused = memory.check_action("cus_agent", "offer_upgrade")
    assert isinstance(refused, ActionCheck)
    assert not refused and refused.denied
    assert "open_problem_blocks_selling" in refused.rules

    pending = memory.check_action("cus_agent", "issue_credit", {"amount": 25})
    assert pending.requires_approval and pending.approval and pending.approval.is_pending

    approver = _dashboard_key(base_url, "sdk-approver", ["memory:read", "approvals:decide"])
    with MemoryClient(api_key=approver, base_url=base_url, max_retries=0) as reviewer:
        decided = reviewer.decide_approval(pending.approval.id, "approve", note="ok")
    assert decided.is_approved
    assert memory.wait_for_approval(pending.approval.id, timeout=1).is_approved

    redeemed = memory.check_action("cus_agent", "issue_credit", {"amount": 25}, approval_id=pending.approval.id)
    assert redeemed.allowed and redeemed.approval.status == "used"

    answered = memory.query("cus_agent", "What keeps failing for them?")
    assert answered.run_id
    runs = memory.runs(customer_id="cus_agent")
    assert answered.run_id in {run.id for run in runs} and isinstance(runs[0], AgentRun)
    explained = memory.explain_run(answered.run_id)
    assert isinstance(explained, RunExplanation) and explained.narrative
    assert any(check.action == "issue_credit" for check in memory.checks(customer_id="cus_agent"))
    assert memory.my_profile() is None


def test_the_agent_middleware_runs_the_loop(memory, live_server):
    from ai_memory import ActionDenied, ApprovalRequired, MemoryAgent

    base_url, _ = live_server
    memory.upsert_customer("cus_loop")
    memory.remember("cus_loop", "Their CSV import keeps timing out.", type="problem")
    seen: dict[str, str] = {}

    def model(prompt: str, message: str) -> str:
        seen["prompt"] = prompt
        return "Sorry about the import — I have raised it with engineering."

    with MemoryAgent(memory, "cus_loop", agent="loop-bot", conversation_id="ticket-77") as agent:
        reply = agent.respond("The CSV import timed out again.", model)
        assert reply.startswith("Sorry")
        assert "CSV import" in seen["prompt"]
        with pytest.raises(ActionDenied) as refused:
            agent.guard("offer_upgrade")
        assert "open problem" in str(refused.value)
        with pytest.raises(ApprovalRequired) as waiting:
            agent.guard("issue_credit", amount=10)
        assert waiting.value.approval is not None
        session_id = agent.session.id

    closed = memory.get_session(session_id)
    assert not closed.is_open and closed.turn_count >= 2
    # The checks were filed with the conversation they happened in.
    assert {check.action for check in memory.checks(session_id=session_id)} >= {"offer_upgrade", "issue_credit"}


def test_a_profile_bound_key_reads_through_its_profile(memory, live_server):
    base_url, _ = live_server
    admin = _dashboard_key(base_url, "sdk-admin", ["admin"])
    with MemoryClient(api_key=admin, base_url=base_url, max_retries=0) as owner:
        profile = owner.create_agent_profile(
            "sdk-support", readable_types=["problem"], denied_actions=["offer_discount"]
        )
    assert profile.may("offer_upgrade") and not profile.may("offer_discount")
    bound = _dashboard_key(base_url, "sdk-support-key", ["memory:read", "customers:read"], profile.id)
    memory.upsert_customer("cus_profile")
    memory.remember("cus_profile", "They prefer email over phone.", type="preference")
    memory.remember("cus_profile", "Their SSO login loops forever.", type="problem")
    with MemoryClient(api_key=bound, base_url=base_url, max_retries=0) as support:
        assert support.my_profile().name == "sdk-support"
        types = {item.type for item in support.memories("cus_profile")}
        assert types == {"problem"}


def test_the_mcp_server_over_http(live_server):
    """An MCP client's view: Streamable HTTP, the caller's own key, real tools on real data."""
    import httpx
    from ai_memory.mcp import ClientCache, MCPServer, make_http_server

    base_url, api_key = live_server
    with MemoryClient(api_key=api_key, base_url=base_url, max_retries=0) as setup:
        setup.upsert_customer("cus_mcp", name="Initech")
        setup.remember("cus_mcp", "Their TPS report export crashes on large files.", type="problem")

    port = _free_port()
    mcp = make_http_server(
        MCPServer(ClientCache(lambda key: MemoryClient(api_key=key, base_url=base_url, max_retries=0))),
        port=port,
    )
    thread = threading.Thread(target=mcp.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/mcp"
    auth = {"Authorization": f"Bearer {api_key}", "Accept": "application/json, text/event-stream"}

    def call(message, headers=auth):
        return httpx.post(url, json=message, headers=headers, timeout=10)

    try:
        init = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest"}}})
        assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "memora"
        assert call({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202

        tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
        assert "customer_brief" in {tool["name"] for tool in tools}

        brief = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "customer_brief", "arguments": {"customer_id": "cus_mcp"}}}).json()["result"]
        assert brief["isError"] is False and brief["content"][0]["text"].startswith("# Initech")
        assert "TPS report" in brief["content"][0]["text"]

        verdict = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "check_action", "arguments": {"customer_id": "cus_mcp", "action": "offer_upgrade"}}}).json()["result"]
        assert verdict["content"][0]["text"].startswith("DENIED")
        assert verdict["structuredContent"]["decision"] == "deny"

        asked = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "ask_memory", "arguments": {"customer_id": "cus_mcp", "question": "What crashes for them?"}}}).json()["result"]
        run_id = asked["structuredContent"]["run_id"]
        explained = call({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "explain_answer", "arguments": {"run_id": run_id}}}).json()["result"]
        assert explained["isError"] is False and "asked" in explained["content"][0]["text"]

        unknown = call({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_health", "arguments": {"customer_id": "nobody"}}}).json()["result"]
        assert unknown["isError"] is True and "404" in unknown["content"][0]["text"]

        no_key = call({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "get_health", "arguments": {"customer_id": "cus_mcp"}}}, headers={})
        assert no_key.status_code == 401
        assert httpx.get(url).status_code == 405
        foreign = call({"jsonrpc": "2.0", "id": 9, "method": "ping"}, headers={**auth, "Origin": "https://evil.example"})
        assert foreign.status_code == 403
    finally:
        mcp.shutdown()
        mcp.server_close()
