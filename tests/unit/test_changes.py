"""What changed, detected from the records that already exist (§26 4.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from memory_engine.changes import (
    ChangeInputs,
    cited_ids,
    detect,
    fact_diff,
    shape,
    state_of,
    summarise,
)
from memory_engine.policy import WITHHELD

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


def memory(ident, kind, content, *, days_ago=1.0, status="active", superseded_by=None, evidence_count=1, **meta):
    moment = NOW - timedelta(days=days_ago)
    return SimpleNamespace(
        id=ident,
        type=kind,
        content=content,
        meta=meta,
        status=status,
        superseded_by=superseded_by,
        first_seen_at=moment,
        last_seen_at=moment,
        evidence_count=evidence_count,
    )


def version(reason, *, days_ago=1.0):
    return SimpleNamespace(reason=reason, created_at=NOW - timedelta(days=days_ago))


def state(track, previous, current, *, days_ago=1.0, seconds=0.0, source="auto", transition="moved", evaluation=None):
    return (
        SimpleNamespace(
            track=track,
            previous_state=previous,
            state=current,
            source=source,
            transition=transition,
            reason=None,
            evidence=["m1"],
            entered_at=NOW - timedelta(days=days_ago) + timedelta(seconds=seconds),
        ),
        evaluation or {},
    )


def inputs(**overrides) -> ChangeInputs:
    return ChangeInputs(since=SINCE, until=NOW, **overrides)


def kinds(changes):
    return [(change.type, change.kind) for change in changes]


# -------------------------------------------------------------------- memories


def test_a_problem_resolved_in_the_window_says_what_it_replaced():
    old = memory("p0", "problem", "The payroll export fails every night.", days_ago=20, status="superseded", superseded_by="p1")
    fixed = memory("p1", "problem", "The payroll export works again.", days_ago=2, resolved=True, supersedes="p0")
    opened = memory("p2", "problem", "SSO login loops back to the start page.", days_ago=3)
    changes = detect(inputs(memories=[fixed, opened], related={"p0": old}))
    assert kinds(changes) == [("problem", "resolved"), ("problem", "opened")]
    resolved = changes[0]
    assert resolved.title == "Resolved: The payroll export works again."
    assert resolved.before == "The payroll export fails every night."
    assert resolved.subject == "p1" and resolved.before_id == "p0"
    assert resolved.evidence == ["p1", "p0"]


def test_a_plan_change_reads_before_from_the_statement_it_follows():
    before = memory("s0", "subscription", "The customer is on the Starter plan.", days_ago=60)
    upgrade = memory("s1", "subscription", "The customer upgraded from the Starter plan to the Pro plan.", days_ago=2)
    change = detect(inputs(memories=[upgrade], previous_subscription=before))[0]
    assert (change.type, change.kind) == ("subscription", "changed")
    assert change.title == "Upgraded from Starter to Pro"
    assert change.before == "The customer is on the Starter plan."
    assert change.detail == {"plan": "pro", "previous_plan": "starter", "direction": "upgraded"}

    cancelled = memory("s2", "subscription", "The customer cancelled their Pro subscription.", days_ago=1)
    latest = detect(inputs(memories=[upgrade, cancelled], previous_subscription=before))[0]
    assert (latest.kind, latest.title) == ("cancelled", "Cancelled the Pro plan")
    assert latest.before_id == "s1"  # chained to the statement before it, not the old one


def test_preferences_intents_relationships_and_facts():
    old = memory("f0", "preference", "Prefers email.", days_ago=90, status="superseded", superseded_by="f1")
    found = detect(
        inputs(
            memories=[
                memory("f1", "preference", "Prefers WhatsApp now.", days_ago=2, supersedes="f0"),
                memory("i1", "intent", "The customer will cancel unless the sync is fixed.", days_ago=3),
                memory("r1", "relationship", "Priya is the new head of finance.", days_ago=4),
                memory("x1", "fact", "The customer has 40 stores.", days_ago=5),
                memory("g1", "goal", "Wants to roll out to every store.", days_ago=5),
                memory("y1", "summary", "Conversation summary.", days_ago=5),
            ],
            related={"f0": old},
        )
    )
    assert kinds(found) == [
        ("preference", "changed"),
        ("intent", "expressed"),
        ("relationship", "added"),
        ("fact", "added"),
    ]
    assert found[0].before == "Prefers email."
    assert found[1].title.startswith("Cancellation intent:")
    assert found[1].detail == {"kinds": ["cancellation"]}


def test_rejected_and_corrected_memories_are_not_changes_of_their_own():
    rejected = memory("p1", "problem", "The export is broken.", days_ago=2, status="superseded", human_rejected=True)
    wrong = memory("p2", "problem", "Billing charges twice.", days_ago=3, status="superseded", superseded_by="p3")
    right = memory("p3", "problem", "Billing charged twice in August only.", days_ago=3, corrects="p2")
    found = detect(
        inputs(
            memories=[rejected, wrong, right],
            related={"p3": right, "p2": wrong},
            versions=[(version("feedback_corrected", days_ago=1), wrong)],
        )
    )
    assert kinds(found) == [("memory", "corrected"), ("problem", "opened")]
    corrected = found[0]
    assert corrected.before == "Billing charges twice."
    assert corrected.after == "Billing charged twice in August only."
    assert corrected.subject == "p3" and corrected.before_id == "p2"


def test_a_problem_reported_again_is_one_change_however_often():
    old = memory("p1", "problem", "Reports load slowly.", days_ago=40, evidence_count=5)
    new = memory("p2", "problem", "Invoices fail to send.", days_ago=2)
    found = detect(
        inputs(
            memories=[new],
            versions=[
                (version("repeated_evidence", days_ago=3), old),
                (version("consolidation_near_duplicate", days_ago=1), old),
                (version("repeated_evidence", days_ago=1), new),  # opened in the window
            ],
        )
    )
    # Newest first: the last report (a day ago) is newer than the opening (two days ago).
    assert kinds(found) == [("problem", "recurring"), ("problem", "opened")]
    recurring = found[0]
    assert recurring.title == "Reported again (2 times): Reports load slowly."
    assert recurring.detail["times"] == 2


def test_strong_feedback_is_quoted_and_the_trend_counted():
    found = detect(
        inputs(
            memories=[
                memory("b1", "feedback", "Terrible support experience.", days_ago=1, sentiment={"polarity": -0.8}),
                memory("b2", "feedback", "The export is a bit slow.", days_ago=2, sentiment={"polarity": -0.3}),
                memory("b3", "feedback", "The new dashboard is fantastic.", days_ago=3, sentiment={"polarity": 0.9}),
            ],
            prior_feedback=[memory("b0", "feedback", "Meh.", days_ago=10, sentiment={"polarity": -0.4})],
        )
    )
    assert ("feedback", "negative") in kinds(found)
    assert ("feedback", "positive") in kinds(found)
    trend = next(change for change in found if change.kind == "rose")
    assert trend.detail == {"negative": 2, "negative_before": 1}
    assert trend.subject is None  # a count is shown to every reader


# ------------------------------------------------------------ goals, lifecycle


def test_goal_evidence_in_the_window_becomes_changes():
    goal = SimpleNamespace(
        id="goal_1",
        statement="Roll out to every store",
        status="achieved",
        progress=1.0,
        evidence=[
            {"kind": "stated", "memory_id": "m1", "at": (NOW - timedelta(days=30)).isoformat()},
            {"kind": "progress", "memory_id": "m2", "at": (NOW - timedelta(days=5)).isoformat()},
            {"kind": "progress", "memory_id": "m3", "at": (NOW - timedelta(days=4)).isoformat()},
            {"kind": "achieved", "memory_id": "m4", "at": (NOW - timedelta(days=1)).isoformat()},
        ],
    )
    found = detect(inputs(goals=[goal]))
    assert kinds(found) == [("goal", "achieved"), ("goal", "progressed")]
    assert found[0].title == "Goal achieved: Roll out to every store"
    assert found[1].evidence == ["goal_1", "m3"]  # the latest progress in the window
    assert found[1].after == "progressing"


def test_lifecycle_moves_carry_reasons_but_first_placement_does_not_count():
    evaluation = {"decisive": [{"fact": "problems.open_count", "actual": 3}, {"fact": "activity.change_pct", "actual": -47.0}]}
    found = detect(
        inputs(
            states=[
                state("engagement", None, "new", days_ago=6, source="initial"),
                state("engagement", "new", "activated", days_ago=6, seconds=0.2),
                state("engagement", "activated", "at_risk", days_ago=2, transition="at_risk", evaluation=evaluation),
            ],
            track_labels={"engagement": "Engagement"},
        )
    )
    assert kinds(found) == [("lifecycle", "moved")]
    move = found[0]
    assert move.title == "Engagement: activated → at risk"
    assert move.track == "engagement"
    assert move.reasons == ["3 unresolved problems", "activity down 47%"]


# ----------------------------------------------------------------- snapshots


def test_then_and_now_give_health_risk_trajectory_and_signals():
    then = {
        "health.band": "healthy", "health.score": 82.0, "signals.churn_risk": 0.1,
        "signals.trajectory": "growing", "signals.active": ["expansion_language"],
        "subscription.plan": "pro", "preferences.channel": "email",
    }
    now = {
        "health.band": "at_risk", "health.score": 54.0, "signals.churn_risk": 0.45,
        "signals.trajectory": "declining", "signals.active": ["repeat_problem"],
        "subscription.plan": "enterprise", "preferences.channel": "whatsapp",
    }
    moment = NOW - timedelta(days=2)
    found = detect(inputs(then=then, now=now, moments={"health.band": moment}, signal_labels={"repeat_problem": "a problem keeps coming back"}))
    assert set(kinds(found)) == {
        ("health", "crossed"), ("risk", "rose"), ("trajectory", "changed"),
        ("subscription", "changed"), ("preference", "changed"),
        ("signal", "started"), ("signal", "stopped"),
    }
    crossed = next(change for change in found if change.type == "health")
    assert crossed.title == "Health moved from healthy to at risk"
    assert (crossed.before, crossed.after, crossed.detected_at) == ("healthy (82)", "at risk (54)", moment)
    started = next(change for change in found if change.kind == "started")
    assert started.title == "Signal started: a problem keeps coming back"


def test_snapshots_do_not_repeat_what_a_memory_already_said():
    then = {"subscription.plan": "starter"}
    now = {"subscription.plan": "pro"}
    upgrade = memory("s1", "subscription", "The customer upgraded to the Pro plan.", days_ago=2)
    found = detect(inputs(memories=[upgrade], then=then, now=now))
    assert kinds(found) == [("subscription", "changed")]
    assert found[0].source == "memory"


def test_withheld_values_are_never_compared():
    found = detect(inputs(then={"subscription.plan": WITHHELD}, now={"subscription.plan": "pro"}))
    assert found == []
    # Without anything recorded before the window, there is no "then" to compare.
    assert detect(inputs(then=None, now={"health.band": "at_risk"})) == []


def test_activity_against_the_window_before():
    assert kinds(detect(inputs(events_now=0, events_before=12))) == [("activity", "quiet")]
    assert kinds(detect(inputs(events_now=8, events_before=0))) == [("activity", "rose")]
    fell = detect(inputs(events_now=8, events_before=15))
    assert fell[0].title == "Activity down 47%: 8 events against 15"
    assert detect(inputs(events_now=4, events_before=3)) == []  # too small to mean anything
    # A customer who did not exist for the whole window before has nothing to compare with.
    newcomer = inputs(events_now=8, events_before=0, customer_since=SINCE - timedelta(days=2))
    assert detect(newcomer) == []
    veteran = inputs(events_now=8, events_before=0, customer_since=SINCE - timedelta(days=30))
    assert kinds(detect(veteran)) == [("activity", "rose")]


# -------------------------------------------------------------------- readers


def test_a_reader_loses_what_they_may_not_see_and_is_told_how_much():
    before = memory("s0", "subscription", "The customer is on the Starter plan.", days_ago=60)
    upgrade = memory("s1", "subscription", "The customer upgraded to the Pro plan.", days_ago=2)
    secret = memory("p1", "problem", "Payroll data for the HR team is exposed.", days_ago=1)
    found = detect(inputs(memories=[upgrade, secret], previous_subscription=before, events_now=0, events_before=9))
    assert cited_ids(found) >= {"s0", "s1", "p1"}

    shown, withheld = shape(found, {"p1", "s0"})
    assert withheld == 1
    assert ("problem", "opened") not in kinds(shown)
    plan = next(change for change in shown if change.type == "subscription")
    assert plan.before == WITHHELD
    assert "previous_plan" not in plan.detail
    assert plan.evidence == ["s1"]
    assert plan.title == "Upgraded to Pro"  # the title only ever quoted the subject
    assert ("activity", "quiet") in kinds(shown)

    unchanged, none = shape(found, set())
    assert none == 0 and len(unchanged) == len(found)


# -------------------------------------------------------------------- summary


def test_the_summary_is_one_readable_sentence():
    old = memory("p0", "problem", "Sync fails.", days_ago=20, status="superseded", superseded_by="p1")
    found = detect(
        inputs(
            memories=[
                memory("s1", "subscription", "The customer upgraded from the Starter plan to the Pro plan.", days_ago=3),
                memory("p1", "problem", "Sync works again.", days_ago=2, resolved=True, supersedes="p0"),
                memory("p2", "problem", "Invoices fail to send.", days_ago=1),
                memory("i1", "intent", "They may cancel if invoices keep failing.", days_ago=1),
            ],
            related={"p0": old},
            then={"health.band": "healthy", "health.score": 80.0},
            now={"health.band": "at_risk", "health.score": 55.0},
            events_now=4,
            events_before=12,
        )
    )
    text = summarise(found, lead="In the last 7 days")
    assert text == (
        "In the last 7 days: upgraded from Starter to Pro; a new problem; a problem resolved; "
        "said they may cancel; health moved from healthy to at risk; activity down 67%."
    )
    assert summarise([], lead="Since 1 Sep 2026") == "Since 1 Sep 2026: nothing material changed."


def test_state_of_and_the_fact_diff():
    then = {"subscription.plan": "starter", "health.band": "healthy", "health.score": 80.0,
            "problems.entities": ["sync"], "lifecycle.engagement": "adopting", "lifecycle.engagement.days_in_state": 3.0}
    now = {"subscription.plan": "pro", "health.band": "healthy", "health.score": 80.0001,
           "problems.entities": ["sync", "invoices"], "lifecycle.engagement": "at_risk",
           "preferences.channel": WITHHELD}
    assert state_of(then)["tracks"] == {"engagement": "adopting"}
    assert state_of(None) is None
    diff = {entry["fact"]: entry for entry in fact_diff(then, now)}
    assert set(diff) == {"subscription.plan", "problems.entities", "lifecycle.engagement"}
    assert diff["problems.entities"]["added"] == ["invoices"]
    assert fact_diff(None, now) == []


def test_a_person_moving_a_customer_is_always_a_change():
    found = detect(
        inputs(
            states=[
                state("lifecycle", None, "new", days_ago=3, source="initial"),
                state("lifecycle", "new", "at_risk", days_ago=3, seconds=0.1),
                # Half a second later, by hand: deliberate, however soon.
                state("lifecycle", "at_risk", "active", days_ago=3, seconds=0.5, source="manual", transition=None),
            ],
        )
    )
    assert [change.title for change in found] == ["Lifecycle: at risk → active (set by hand)"]
    assert found[0].reasons == ["set by hand"]
    assert found[0].detail["manual"] is True


def test_a_duplicate_the_sweep_merged_is_not_a_change_of_its_own():
    survivor = memory("p1", "problem", "Invoices fail to send.", days_ago=3)
    duplicate = memory("p2", "problem", "Invoices are failing to send.", days_ago=2, status="superseded", superseded_by="p1")
    found = detect(
        inputs(memories=[survivor, duplicate], related={"p1": survivor}, versions=[(version("offline_consolidation"), duplicate)])
    )
    assert [change.subject for change in found] == ["p1"]


def test_a_state_only_passed_through_ranks_below_where_the_customer_ended_up():
    found = detect(
        inputs(
            states=[
                state("engagement", "new", "activated", days_ago=5, transition="activated"),
                state("engagement", "activated", "at_risk", days_ago=2, transition="at_risk"),
                state("lifecycle", "onboarding", "at_risk", days_ago=2, transition="at_risk"),
            ],
            track_labels={"engagement": "Engagement", "lifecycle": "Lifecycle"},
        )
    )
    by_title = {change.title: change for change in found}
    passed = by_title["Engagement: new → activated"]
    landed = by_title["Engagement: activated → at risk"]
    assert passed.detail["passed_through"] is True and "passed_through" not in landed.detail
    assert passed.importance == landed.importance / 2
    assert by_title["Lifecycle: onboarding → at risk"].importance == landed.importance
    # Each track is named, the primary one included, with where it ended up.
    ordered = sorted(found, key=lambda change: (change.importance, change.detected_at), reverse=True)
    text = summarise(ordered, lead="In the last 7 days")
    assert "engagement moved to at risk" in text and "lifecycle moved to at risk" in text
    assert "moved to activated" not in text
