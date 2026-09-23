"""Predictive signals and next-best-action rules.

These are the two modules that make claims about the *future*, so the bar for them is that
every claim is traceable to an observation and that the same history always produces the
same forecast.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.enums import MemoryType, SignalDirection, Trajectory
from common.time import utcnow
from memory_engine.analytics.recommend import priority_for, recommend, summarise
from memory_engine.analytics.signals import (
    WINDOW_DAYS,
    ActivityWindow,
    Baseline,
    GoalSnapshot,
    compute,
)
from nlp.answer import MemoryView

NOW = utcnow()


def memory(
    id: str,
    type: MemoryType,
    content: str,
    *,
    age_days: float = 1,
    last_seen_days: float | None = None,
    evidence_count: int = 1,
    importance: float = 0.5,
    urgency: float = 0.0,
    resolved: bool = False,
) -> MemoryView:
    attributes: dict[str, object] = {"sentiment": {"urgency": urgency}}
    if resolved:
        attributes["resolved"] = True
    return MemoryView(
        id=id,
        type=type,
        content=content,
        importance=importance,
        first_seen_at=NOW - timedelta(days=age_days),
        last_seen_at=NOW - timedelta(days=last_seen_days if last_seen_days is not None else age_days),
        evidence_count=evidence_count,
        attributes=attributes,
    )


def keys(report) -> set[str]:
    return {signal.key for signal in report.signals}


# ------------------------------------------------------------------ trajectory


def test_a_customer_with_nothing_recorded_is_steady_and_unconfident():
    report = compute(memories=[], now=NOW)
    assert report.trajectory == Trajectory.STEADY
    assert report.signals == []
    assert report.confidence <= 0.2
    assert "nothing has changed" in report.headline


def test_problems_accelerating_reads_as_declining():
    memories = [
        memory("m1", MemoryType.PROBLEM, "the sync failed", age_days=2),
        memory("m2", MemoryType.PROBLEM, "the export is broken", age_days=4),
        memory("m3", MemoryType.PROBLEM, "invoices are wrong", age_days=6),
        memory("m4", MemoryType.PROBLEM, "an old glitch", age_days=20),
    ]
    report = compute(memories=memories, health_score=55, now=NOW)
    assert "escalating_problems" in keys(report)
    assert report.trajectory == Trajectory.DECLINING
    assert report.churn_risk > 0.4


def test_a_stored_baseline_beats_the_pressure_heuristic():
    """A measured 20-point fall is a stronger statement than any count of signals."""
    memories = [memory("m1", MemoryType.FEEDBACK, "this is great", importance=0.2, age_days=3)]
    baseline = Baseline(health_score=85.0, churn_risk=0.15, captured_at=NOW - timedelta(days=20))
    report = compute(memories=memories, health_score=60, baseline=baseline, now=NOW)
    assert report.trajectory == Trajectory.DECLINING
    assert "health_slide" in keys(report)
    assert report.measurements["health_delta"] == pytest.approx(-25.0)


def test_health_climbing_reads_as_improving():
    baseline = Baseline(health_score=55.0, churn_risk=0.45, captured_at=NOW - timedelta(days=20))
    report = compute(memories=[], health_score=80, baseline=baseline, now=NOW)
    assert report.trajectory == Trajectory.IMPROVING
    assert "health_climb" in keys(report)


# --------------------------------------------------------------------- signals


def test_engagement_decay_needs_a_real_prior_window():
    """Two events against one is not a trend; ten against two is."""
    quiet = compute(
        memories=[], activity=ActivityWindow(recent_events=1, prior_events=2), now=NOW
    )
    assert "engagement_decay" not in keys(quiet)

    falling = compute(
        memories=[], activity=ActivityWindow(recent_events=2, prior_events=10), now=NOW
    )
    assert "engagement_decay" in keys(falling)
    assert falling.measurements["engagement_drop"] == pytest.approx(0.8)


def test_silence_is_measured_from_the_last_event():
    report = compute(
        memories=[],
        activity=ActivityWindow(last_event_at=NOW - timedelta(days=40)),
        now=NOW,
    )
    signal = next(s for s in report.signals if s.key == "silence")
    assert "40 days" in signal.rationale
    assert signal.direction == SignalDirection.RISK


def test_churn_language_is_found_by_content_as_well_as_by_score():
    memories = [
        memory("m1", MemoryType.INTENT, "we are evaluating a competitor", age_days=2)
    ]
    report = compute(memories=memories, now=NOW)
    assert "churn_language" in keys(report)


def test_an_open_problem_caps_the_expansion_score():
    """Nobody buys more while something of theirs is broken."""
    upgrade = memory("m1", MemoryType.SUBSCRIPTION, "we want to upgrade to enterprise", age_days=1)
    happy = compute(memories=[upgrade], health_score=85, now=NOW)

    broken = memory("m2", MemoryType.PROBLEM, "the api keeps timing out", age_days=1)
    blocked = compute(memories=[upgrade, broken], health_score=85, now=NOW)

    assert happy.expansion_score > blocked.expansion_score
    assert "expansion_intent" in keys(happy)


def test_a_later_downgrade_cancels_an_earlier_upgrade():
    """Someone who upgraded last week and cancelled today is not an expansion opportunity."""
    upgraded = memory(
        "m1", MemoryType.SUBSCRIPTION, "upgraded from the Starter plan to the Pro plan", age_days=11
    )
    cancelled = memory(
        "m2", MemoryType.SUBSCRIPTION, "downgraded to Starter and asked about cancelling", age_days=1
    )

    before = compute(memories=[upgraded], health_score=70, now=NOW)
    assert "expansion_intent" in keys(before)

    after = compute(memories=[upgraded, cancelled], health_score=30, now=NOW)
    assert "expansion_intent" not in keys(after)
    assert after.expansion_score == 0.0
    assert after.trajectory == Trajectory.DECLINING


def test_advocacy_only_fires_when_nothing_is_open():
    praise = memory("m1", MemoryType.FEEDBACK, "the team loves it", importance=0.3, age_days=2)
    assert "advocacy" in keys(compute(memories=[praise], now=NOW))

    broken = memory("m2", MemoryType.PROBLEM, "login is down", age_days=1)
    assert "advocacy" not in keys(compute(memories=[praise, broken], now=NOW))


def test_a_stalled_goal_is_a_risk_signal():
    goal = GoalSnapshot(
        id="goal_1",
        statement="roll out SSO to the sales team",
        status="stalled",
        progress=0.2,
        last_signal_at=NOW - timedelta(days=45),
    )
    report = compute(memories=[], goals=[goal], now=NOW)
    signal = next(s for s in report.signals if s.key == "goal_stalled")
    assert "SSO" in signal.rationale
    assert signal.direction == SignalDirection.RISK


def test_every_signal_carries_a_rationale_and_bounded_strength():
    memories = [
        memory("m1", MemoryType.PROBLEM, "the sync keeps failing", age_days=20, evidence_count=5),
        memory("m2", MemoryType.PROBLEM, "billing is wrong again", age_days=1),
        memory("m3", MemoryType.INTENT, "thinking about cancelling", age_days=1),
    ]
    report = compute(
        memories=memories,
        activity=ActivityWindow(recent_events=1, prior_events=12, last_event_at=NOW),
        health_score=35,
        now=NOW,
    )
    assert report.signals
    for signal in report.signals:
        assert signal.rationale.strip()
        assert 0 < signal.strength <= 1
        assert signal.horizon_days > 0
    assert 0 <= report.churn_risk <= 1
    assert report.signals == sorted(report.signals, key=lambda s: -s.strength)


def test_the_forecast_is_reproducible():
    memories = [memory("m1", MemoryType.PROBLEM, "it broke", age_days=3)]
    first = compute(memories=memories, health_score=60, now=NOW)
    second = compute(memories=memories, health_score=60, now=NOW)
    assert first.as_dict() == second.as_dict()


def test_windows_do_not_overlap():
    """A memory is counted in exactly one of the two comparison windows."""
    on_the_boundary = memory("m1", MemoryType.PROBLEM, "edge case", age_days=WINDOW_DAYS)
    report = compute(memories=[on_the_boundary], now=NOW)
    assert report.measurements["problems_recent"] == 0
    assert report.measurements["problems_prior"] == 1


# ------------------------------------------------------------- recommendations


def test_the_worst_open_problem_becomes_the_first_action():
    memories = [
        memory("m1", MemoryType.PROBLEM, "csv export is slow", age_days=2, urgency=0.1),
        memory(
            "m2",
            MemoryType.PROBLEM,
            "production is down for our whole team",
            age_days=9,
            urgency=0.9,
            evidence_count=3,
        ),
    ]
    report = compute(memories=memories, health_score=40, now=NOW)
    actions = recommend(memories=memories, report=report, health_score=40, now=NOW)

    assert actions[0].priority == "now"
    assert "m2" in actions[0].memory_ids
    assert all(action.rationale.strip() for action in actions)


def test_recommendations_are_deterministic_and_capped():
    memories = [
        memory("m1", MemoryType.PROBLEM, "sync broken", age_days=20, evidence_count=4),
        memory("m2", MemoryType.INTENT, "we might cancel", age_days=1),
        memory("m3", MemoryType.PREFERENCE, "please contact me on slack", age_days=30),
        memory("m4", MemoryType.FEEDBACK, "the reports are lovely", importance=0.2, age_days=2),
    ]
    report = compute(
        memories=memories,
        activity=ActivityWindow(recent_events=1, prior_events=9),
        health_score=42,
        now=NOW,
    )
    first = recommend(memories=memories, report=report, health_score=42, now=NOW)
    second = recommend(memories=memories, report=report, health_score=42, now=NOW)

    assert [a.key for a in first] == [b.key for b in second]
    assert len(first) <= 5
    assert first == sorted(first, key=lambda a: (-a.urgency, a.key))


def test_nothing_is_pitched_to_a_customer_with_an_open_problem():
    memories = [
        memory("m1", MemoryType.SUBSCRIPTION, "we want to upgrade to enterprise", age_days=1),
        memory("m2", MemoryType.PROBLEM, "the importer crashes", age_days=1),
    ]
    report = compute(memories=memories, health_score=80, now=NOW)
    actions = recommend(memories=memories, report=report, health_score=80, now=NOW)
    assert "expansion_offer" not in {action.key for action in actions}
    assert "ask_for_advocacy" not in {action.key for action in actions}


def test_a_healthy_quiet_customer_gets_no_busywork():
    memories = [memory("m1", MemoryType.FACT, "they are on the pro plan", age_days=60)]
    report = compute(memories=memories, health_score=88, now=NOW)
    actions = recommend(memories=memories, report=report, health_score=88, now=NOW)
    assert actions == []
    assert summarise(actions) == "Nothing needs doing."


def test_a_stated_contact_preference_is_always_surfaced():
    memories = [memory("m1", MemoryType.PREFERENCE, "email me, never call", age_days=100)]
    report = compute(memories=memories, health_score=75, now=NOW)
    actions = recommend(memories=memories, report=report, health_score=75, now=NOW)
    assert "respect_contact_preference" in {action.key for action in actions}


def test_every_recommendation_cites_something():
    memories = [
        memory("m1", MemoryType.PROBLEM, "the sync keeps failing", age_days=25, evidence_count=4),
        memory("m2", MemoryType.INTENT, "we are looking at competitors", age_days=2),
    ]
    report = compute(memories=memories, health_score=38, now=NOW)
    goals = [
        GoalSnapshot(
            id="goal_1",
            statement="migrate billing to us",
            status="stalled",
            progress=0.3,
            last_signal_at=NOW - timedelta(days=50),
        )
    ]
    actions = recommend(
        memories=memories, report=report, goals=goals, health_score=38, now=NOW
    )
    for action in actions:
        assert action.memory_ids or action.goal_ids or action.signals
        assert action.playbook


@pytest.mark.parametrize(
    ("urgency", "expected"),
    [(0.9, "now"), (0.75, "now"), (0.5, "soon"), (0.44, "when_you_can"), (0.0, "when_you_can")],
)
def test_priority_bands(urgency: float, expected: str):
    assert priority_for(urgency) == expected
