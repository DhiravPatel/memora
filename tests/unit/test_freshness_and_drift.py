"""Freshness and drift, as pure rules (§26 5.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.drift_service import billing_plan, feature_of, plan_direction
from memory_engine.context.builder import ContextBuilder
from memory_engine.drift import (
    Billing,
    Contact,
    DriftSettings,
    channel_drift,
    plan_drift,
    quiet_problem,
    settings_for,
    usage_drift,
)
from memory_engine.freshness import FLOOR, assess, counts, evidence_at, note, windows
from memory_engine.schemas import ScoredMemory
from nlp.entities import contact_channel

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SETTINGS = DriftSettings()


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def memory(kind: str = "preference", *, days: float = 1, confidence: float = 0.9, status: str = "active", **meta):
    return SimpleNamespace(
        id="m1",
        type=kind,
        status=status,
        confidence=confidence,
        importance=0.6,
        content="The customer prefers email.",
        last_seen_at=ago(days),
        expires_at=meta.pop("expires_at", None),
        meta=meta,
    )


# -------------------------------------------------------------------- freshness


def test_states_follow_the_types_window():
    assert assess(memory(days=10), now=NOW, window_days=180).state == "active"
    aging = assess(memory(days=100), now=NOW, window_days=180)
    assert aging.state == "aging"
    assert aging.reasons == ("No new evidence in 3 months; preferences go stale after 6 months.",)
    stale = assess(memory("problem", days=45), now=NOW, window_days=30)
    assert stale.state == "stale" and stale.reasons == ("No new evidence in 6 weeks; problems go stale after 4 weeks.",)
    assert assess(memory("summary", days=45), now=NOW, window_days=30).reasons[0].endswith("summaries go stale after 4 weeks.")


def test_effective_confidence_halves_each_window_but_never_vanishes():
    assert assess(memory(days=0, confidence=0.8), now=NOW, window_days=100).effective_confidence == pytest.approx(0.8)
    assert assess(memory(days=100, confidence=0.8), now=NOW, window_days=100).effective_confidence == pytest.approx(0.4)
    ancient = assess(memory(days=5000, confidence=0.8), now=NOW, window_days=100)
    assert ancient.effective_confidence == pytest.approx(0.8 * FLOOR)


def test_a_person_confirming_is_new_evidence():
    confirmed = memory(days=300, confirmed_at=ago(2).isoformat())
    assert evidence_at(confirmed) == ago(2)
    assert assess(confirmed, now=NOW, window_days=180).state == "active"


def test_precedence_superseded_expired_conflicted_outdated_then_age():
    assert assess(memory(status="superseded"), now=NOW, window_days=180).state == "superseded"
    expired = assess(memory(expires_at=ago(1)), now=NOW, window_days=180)
    assert expired.state == "expired" and expired.effective_confidence == 0.0
    conflicted = assess(memory(days=10), now=NOW, window_days=180, contradicted_at=ago(3))
    assert conflicted.state == "conflicted" and conflicted.reasons[0].startswith("Contradicted on 22 Sep 2026")
    # A contradiction before the latest evidence has been answered by it.
    assert assess(memory(days=10), now=NOW, window_days=180, contradicted_at=ago(20)).state == "active"
    flagged = assess(memory(days=10), now=NOW, window_days=180, drift=[{"id": "d1", "kind": "channel", "summary": "Mostly WhatsApp."}])
    assert flagged.state == "outdated" and flagged.reasons == ("Mostly WhatsApp.",)
    assert flagged.effective_confidence < assess(memory(days=10), now=NOW, window_days=180).effective_confidence
    assert counts([conflicted, flagged, expired])["conflicted"] == 1


def test_windows_merge_the_projects_over_the_defaults():
    merged = windows({"freshness_days": {"problem": 10, "preference": 99999, "goal": "soon"}})
    assert merged["problem"] == 10 and merged["preference"] == 3650 and merged["goal"] == 90


def test_the_note_an_agent_reads():
    assert note(assess(memory(days=10), now=NOW, window_days=180)) is None
    assert note(assess(memory("problem", days=45), now=NOW, window_days=30)) == "stale: last evidence 6 weeks ago"
    flagged = assess(memory(), now=NOW, window_days=180, drift=[{"id": "d1", "kind": "channel", "summary": "Mostly WhatsApp."}])
    assert note(flagged) == "possibly outdated: Mostly WhatsApp."


def test_the_context_marks_what_is_not_fresh():
    row = SimpleNamespace(
        id="m1", type="preference", content="The customer prefers email.", importance=0.6, confidence=0.9,
        last_seen_at=ago(200), evidence_count=1, source_event_ids=[], meta={},
    )
    flagged = assess(memory(days=200), now=NOW, window_days=180)
    context = ContextBuilder().build(
        customer={"id": "c1", "name": "Acme"},
        memories=[ScoredMemory(memory=row, score=0.9, strategies={"semantic"})],
        freshness={"m1": flagged},
    )
    item = context.sections["preferences"][0]
    assert item["freshness"] == "stale" and item["freshness_note"] == "stale: last evidence 6 months ago"
    assert "- The customer prefers email. (stale: last evidence 6 months ago)" in context.to_prompt_text()


# ------------------------------------------------------------------- channels


@pytest.mark.parametrize(
    ("event_type", "data", "source", "channel"),
    [
        ("whatsapp_message", {}, "api", "WhatsApp"),
        ("support_message", {"channel": "whatsapp"}, "api", "WhatsApp"),
        ("ticket_created", {"via": {"channel": "email"}}, "zendesk", "email"),
        ("support_message", {"channel": "C0123ABC"}, "slack", "Slack"),
        ("support_message", {}, "intercom", "chat"),
        ("call_logged", {}, "api", "phone"),
        ("support_message", {"channel": "voice"}, "api", "phone"),
        ("support_message", {}, "api", None),
        ("support_reply_sent", {"channel": "email"}, "api", None),
        ("feature_used", {"channel": "email"}, "api", None),
        ("campaign_email_opened", {}, "api", None),
    ],
)
def test_only_the_customer_reaching_out_is_a_contact(event_type, data, source, channel):
    assert contact_channel(event_type, data, source) == channel


def test_channel_drift_needs_enough_contacts_and_a_clear_majority():
    contacts = [Contact("WhatsApp", ago(10 - day), f"e{day}") for day in range(5)] + [Contact("email", ago(1), "e9")]
    found = channel_drift(memory_id="m1", stated="email", since=ago(60), stated_at=ago(60), contacts=contacts, settings=SETTINGS)
    assert found is not None and found.observed == "WhatsApp"
    assert found.summary == "They said they prefer email on 27 Jul 2026; since then 5 of their 6 contacts came through WhatsApp and 1 through email."
    assert found.evidence == ["e4", "e3", "e2", "e1", "e0"]
    assert channel_drift(memory_id="m1", stated="email", since=ago(60), contacts=contacts[:4], settings=SETTINGS) is None
    split = contacts + [Contact("email", ago(2), f"x{n}") for n in range(4)]
    assert channel_drift(memory_id="m1", stated="email", since=ago(60), contacts=split, settings=SETTINGS) is None, "5 of 10 is no majority"
    # Contacts before the preference (or a dismissal) do not count.
    assert channel_drift(memory_id="m1", stated="email", since=ago(3), contacts=contacts, settings=SETTINGS) is None


def test_after_a_dismissal_the_sentence_says_when_counting_restarted():
    contacts = [Contact("WhatsApp", ago(5 - day), f"e{day}") for day in range(5)]
    found = channel_drift(memory_id="m1", stated="email", since=ago(8), stated_at=ago(60), contacts=contacts, settings=SETTINGS)
    assert found is not None
    assert found.summary.startswith("They said they prefer email on 27 Jul 2026; since 17 Sep 2026 5 of their 5 contacts")


def test_plan_drift_reads_the_unbroken_run_of_the_latest_plan():
    billing = [Billing("enterprise", ago(40), "b1"), Billing("pro", ago(30), "b2"), Billing("enterprise", ago(20), "b3"), Billing("enterprise", ago(5), "b4")]
    found = plan_drift(memory_id="m1", stated="pro", since=ago(60), billing=billing, settings=SETTINGS)
    assert found is not None and found.observed == "enterprise" and found.evidence == ["b4", "b3"]
    assert found.counts == {"events": 2, "since_statement": 4}
    assert plan_drift(memory_id="m1", stated="pro", since=ago(60), billing=billing[:3], settings=SETTINGS) is None
    assert plan_drift(memory_id="m1", stated="enterprise", since=ago(60), billing=billing, settings=SETTINGS) is None


def test_quiet_detectors_need_the_customer_to_stay_active():
    found = quiet_problem(memory_id="m1", content="The export times out.", evidence_at=ago(45), activity_since=12, settings=SETTINGS, now=NOW)
    assert found is not None and found.summary == "Not reported in 6 weeks while they stayed active (12 events since) — it may have been fixed."
    assert quiet_problem(memory_id="m1", content="x", evidence_at=ago(45), activity_since=2, settings=SETTINGS, now=NOW) is None, "a quiet customer is silent, not fixed"
    assert quiet_problem(memory_id="m1", content="x", evidence_at=ago(10), activity_since=50, settings=SETTINGS, now=NOW) is None
    # A dismissal restarts the count.
    assert quiet_problem(memory_id="m1", content="x", evidence_at=ago(45), counting_from=ago(5), activity_since=12, settings=SETTINGS, now=NOW) is None
    usage = usage_drift(memory_id="m2", feature="Campaign Builder", last_used_at=ago(75), activity_since=30, settings=SETTINGS, now=NOW)
    assert usage is not None and usage.summary == "No Campaign Builder use in 2 months (last on 12 Jul 2026), while they stayed active: 30 events since."
    assert usage_drift(memory_id="m2", feature="x", last_used_at=None, activity_since=30, settings=SETTINGS, now=NOW) is None


def test_thresholds_come_from_the_project():
    tuned = settings_for({"drift_min_contacts": 3, "drift_min_share": 0.75, "drift_quiet_days": {"problem": 14}, "drift_min_activity": "lots"})
    assert (tuned.min_contacts, tuned.min_share, tuned.quiet_problem_days, tuned.quiet_usage_days, tuned.min_activity) == (3, 0.75, 14, 60, 5)


def test_helpers_for_plans_and_features():
    assert [billing_plan(value) for value in ("Enterprise (annual)", "enterprise_monthly", "Gold tier", "", None)] == [
        "enterprise", "enterprise", "gold tier", None, None,
    ]
    assert (plan_direction("pro", "enterprise"), plan_direction("pro", "starter"), plan_direction("pro", "gold")) == (
        "upgraded", "downgraded", "changed",
    )
    assert feature_of(SimpleNamespace(meta={"feature": "Reports"}, content="x")) == "Reports"
    assert feature_of(SimpleNamespace(meta={}, content="The customer uses the Campaign Builder feature.")) == "Campaign Builder"
    assert feature_of(SimpleNamespace(meta={}, content="The customer likes reports.")) is None
