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
