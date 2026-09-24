"""Reasons in words, opt-outs, the new trend facts and lifecycle tracks (§26 4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from memory_engine.conditions import compile_condition, sanitize_evaluation
from memory_engine.facts import FactInputs, build_facts
from memory_engine.lifecycle import TRACK_TEMPLATES, LifecycleError, compile_tracks
from memory_engine.reasons import reasons, sentence
from nlp.optouts import opt_outs

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def memory(ident: str, kind: str, content: str, days_ago: float = 1.0, **meta):
    moment = NOW - timedelta(days=days_ago)
    return SimpleNamespace(
        id=ident,
        type=kind,
        content=content,
        meta=meta,
        sensitivity="normal",
        first_seen_at=moment,
        last_seen_at=moment,
        importance=0.6,
        confidence=0.8,
        evidence_count=1,
    )


def report(recent: int, prior: int):
    return SimpleNamespace(
        trajectory="declining",
        churn_risk=0.3,
        expansion_score=0.1,
        confidence=0.5,
        measurements={"events_recent": recent, "events_prior": prior},
        signals=[],
        risks=[],
        opportunities=[],
    )


def facts_for(memories: list, *, recent: int = 8, prior: int = 15, tracks=None):
    grouped: dict[str, list] = {}
    for item in memories:
        grouped.setdefault(item.type, []).append(item)
    customer = SimpleNamespace(
        external_id="acme", name="Acme", email=None, created_at=NOW - timedelta(days=90),
        last_event_at=NOW - timedelta(days=1), meta={},
    )
    return build_facts(
        FactInputs(
            customer=customer,
            now=NOW,
            report=report(recent, prior),
            memories_by_type=grouped,
            tracks=tracks or {},
        )
    )


# ------------------------------------------------------------------ sentences


def test_sentences_describe_the_actual_value():
    assert sentence("problems.open_count", 3) == "3 unresolved problems"
    assert sentence("problems.open_count", 1) == "1 unresolved problem"
    assert sentence("activity.change_pct", -46.7) == "activity down 47%"
    assert sentence("feedback.negative_trend", "rising") == "negative feedback increasing"
    assert sentence("subscription.plan", "pro") == "on the pro plan"
    assert sentence("intents.kinds", ["cancellation"]) == "said they may cancel"
    assert sentence("preferences.opt_outs", ["phone", "sales"]) == "asked not to be called; asked for no sales outreach"
    assert sentence("lifecycle.engagement", "power_user") == "engagement is power user"
    assert sentence("customer.metadata.segment", "smb") == "segment is smb"
    assert sentence("problems.open_count", None) is None
    assert sentence("problems.entities", "[withheld]") is None


def test_reasons_come_from_the_decisive_clauses_whichever_way_they_went():
    facts = facts_for([memory("m1", "subscription", "The customer upgraded from Starter to Pro.")])
    condition = compile_condition('not (subscription.plan in ["enterprise"]) and activity.change_pct <= -40')
    evaluation = condition.evaluate(facts)
    assert evaluation.matched
    words = reasons(evaluation)
    assert "on the pro plan" in words
    assert "activity down 47%" in words
    # The stored form renders the same, so transitions recorded earlier get reasons too.
    assert reasons(evaluation.as_dict()) == words


def test_a_sanitised_trace_gives_reasons_without_the_withheld_values():
    facts = facts_for([memory("m1", "problem", "The payroll export fails.")])
    evaluation = compile_condition('problems.entities contains "payroll" or problems.open_count >= 1').evaluate(facts)
    trace = sanitize_evaluation(evaluation.as_dict())
    words = reasons(trace)
    assert all("payroll" not in word for word in words)


# ------------------------------------------------------------------- opt-outs


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please don't call me, email is fine.", ["phone"]),
        ("No sales calls please, and unsubscribe us from the newsletter.", ["sales", "marketing"]),
        ("Do not contact us again.", ["contact"]),
        ("I don't mind calls.", []),
        ("They prefer WhatsApp.", []),
    ],
)
def test_opt_outs(text, expected):
    assert opt_outs(text) == expected


# ---------------------------------------------------------------- new facts


def test_activity_change_and_the_negative_feedback_trend():
    facts = facts_for(
        [
            memory("f1", "feedback", "Reporting is slow and frustrating.", days_ago=2, sentiment={"polarity": -0.6}),
            memory("f2", "feedback", "The export keeps failing, very annoying.", days_ago=5, sentiment={"polarity": -0.7}),
            memory("f3", "feedback", "Terrible support experience.", days_ago=20, sentiment={"polarity": -0.8}),
        ],
        recent=8,
        prior=15,
    )
    assert facts.get("activity.change_pct") == -46.7
    assert facts.get("feedback.recent_negative_count") == 2
    assert facts.get("feedback.prior_negative_count") == 1
    assert facts.get("feedback.negative_trend") == "rising"
    # With nothing before, a percentage is not a number.
    assert facts_for([], recent=4, prior=0).get("activity.change_pct") is None


def test_opt_outs_are_facts_with_evidence():
    facts = facts_for([memory("p1", "preference", "Please don't call us; email works best.")])
    assert facts.get("preferences.opt_outs") == ["phone"]
    assert facts.evidence_for("preferences.opt_outs", "phone") == ["p1"]
    assert compile_condition('preferences.opt_outs contains "phone"').evaluate(facts).matched


def test_every_track_is_a_fact():
    facts = facts_for([], tracks={"engagement": ("adopting", NOW - timedelta(days=12))})
    assert facts.get("lifecycle.engagement") == "adopting"
    assert facts.get("lifecycle.engagement.days_in_state") == 12.0
    assert compile_condition('lifecycle.engagement == "adopting" and lifecycle.engagement.days_in_state > 10').evaluate(facts).matched


# -------------------------------------------------------------------- tracks


def test_the_templates_compile_and_are_named():
    tracks = compile_tracks(TRACK_TEMPLATES)
    assert set(tracks) == {"engagement", "commercial"}
    assert tracks["engagement"].machine.initial == "new"
    assert "power_user" in tracks["engagement"].machine.states
    assert tracks["commercial"].label == "Commercial"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"lifecycle": TRACK_TEMPLATES["engagement"]}, "primary track"),
        ({"Bad Name!": TRACK_TEMPLATES["engagement"]}, "not a valid track name"),
        ({"usage": {"states": ["a"], "transitions": [{"to": "b", "when": "health.score < 1"}]}}, "not a state"),
        ({"usage": {"states": ["a", "b"], "transitions": [{"to": "b", "when": "helth.score < 1"}]}}, "health.score"),
    ],
)
def test_a_broken_track_is_refused(raw, message):
    with pytest.raises(LifecycleError, match=message):
        compile_tracks(raw)


def test_the_engagement_track_moves_on_real_facts():
    tracks = compile_tracks(TRACK_TEMPLATES)
    engagement = tracks["engagement"].machine
    quiet = facts_for([], recent=0, prior=0)
    quiet.values["activity.distinct_features"] = 0
    assert engagement.settle(None, quiet) == []
    busy = facts_for([], recent=12, prior=6)
    busy.values["activity.distinct_features"] = 3
    steps = engagement.settle(None, busy)
    assert [step.target for step in steps] == ["activated", "adopting"]
    assert "uses 3 features" in reasons(steps[-1].evaluation)
