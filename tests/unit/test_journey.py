"""The journey as milestones, as pure rules (§26 6.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from memory_engine.analytics.health import WEIGHTS
from memory_engine.journey import (
    FirstUse,
    Gap,
    JourneyInputs,
    Point,
    Stay,
    cited_ids,
    detect,
    markdown,
    select,
    shape,
    summarise,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def on(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


def memory(ident: str, kind: str, content: str, at: datetime, *, event: str | None = None, reports: int = 1,
           status: str = "active", superseded_by: str | None = None, source: str = "api", **meta):
    return SimpleNamespace(
        id=ident, type=kind, content=content, first_seen_at=at, last_seen_at=at, created_at=at, status=status,
        evidence_count=reports, meta=meta, source_event_ids=[event] if event else [], superseded_by=superseded_by,
        expires_at=None, source=source,
    )


def version(ident: str, at: datetime, event: str):
    return SimpleNamespace(id=ident, created_at=at, source_event_id=event, reason="repeated_evidence")


def point(ident: str, taken: datetime, event: str | None, score: float, band: str, changes=()) -> Point:
    return Point(ident, taken, event, {"health.score": score, "health.band": band}, list(changes))


def stay(ident: str, previous: str | None, state: str, entered: datetime, *, track: str = "lifecycle",
         source: str = "auto", snapshot: str | None = None, evaluation: dict | None = None) -> Stay:
    row = SimpleNamespace(
        id=ident, track=track, previous_state=previous, state=state, entered_at=entered, source=source,
        transition=state, reason=None, evidence=[], snapshot_id=snapshot,
    )
    return Stay(row, evaluation or {})


def inputs(**overrides) -> JourneyInputs:
    base = {
        "now": NOW,
        "customer_name": "Acme",
        "weights": dict(WEIGHTS),
        "track_labels": {"lifecycle": "Lifecycle", "engagement": "Engagement"},
    }
    return JourneyInputs(**{**base, **overrides})


def titles(milestones) -> list[str]:
    return [milestone.title for milestone in milestones]


# ------------------------------------------------------------------ the example


def acme() -> JourneyInputs:
    """The journey from the spec: Shopify, its failures, a goal, health, a downgrade, at risk."""
    problem = memory("m_problem", "problem", "The Shopify sync fails on large orders.", on(5), event="e_problem",
                     reports=3, entity_names=["Shopify"])
    goal_memory = memory("m_goal", "goal", "The customer wants to launch automation.", on(12), event="e_goal")
    downgrade = memory("m_down", "subscription", "The customer downgraded from the Pro plan to the Starter plan.",
                       on(21), event="e_down", plan="starter", previous_plan="pro", direction="downgraded")
    intent = memory("m_intent", "intent", "The customer said they may cancel if the sync keeps failing.", on(23),
                    event="e_intent")
    goal = SimpleNamespace(
        id="goal_1", statement="Launch automation", memory_id="m_goal", opened_at=on(12), last_signal_at=on(12),
        status="open", evidence=[{"kind": "stated", "memory_id": "m_goal", "at": on(12).isoformat()}],
    )
    # Health and the lifecycle were judged when each event was processed — on the 25th, for
    # this imported history — and each snapshot remembers the event behind it.
    points = [
        point("s1", on(25, 9), "e_signup", 82, "healthy"),
        point("s2", on(25, 10), "e_problem", 67, "watch", [{"fact": "problems.open_count", "before": 0, "after": 1}]),
        point("s3", on(25, 11), "e_intent", 48, "at_risk", [{"fact": "intents.kinds", "before": [], "after": ["cancellation"]}]),
    ]
    stays = [
        stay("st0", None, "onboarding", on(25, 9), source="initial", snapshot="s1"),
        stay("st1", "onboarding", "active", on(25, 9) + timedelta(milliseconds=2), snapshot="s1"),
        stay("st2", "active", "at_risk", on(25, 11), snapshot="s3",
             evaluation={"decisive": [{"fact": "intents.kinds", "actual": ["cancellation"]}]}),
    ]
    return inputs(
        customer_since=on(1),
        first_event=("e_signup", "signup", on(1)),
        memories=[problem, goal_memory, downgrade, intent],
        versions=[(version("v1", on(25, 10), "e_r2"), problem), (version("v2", on(25, 10), "e_r3"), problem)],
        goals=[goal],
        stays=stays,
        points=points,
        events={
            "e_signup": (on(1), "signup"), "e_problem": (on(5), "support_message"), "e_r2": (on(6), "support_message"),
            "e_r3": (on(8), "support_message"), "e_intent": (on(23), "support_message"),
        },
        first_uses=[FirstUse("integration", "Shopify", on(1, 11), "e_shop", on(20), 12, "integration_connected")],
    )


def test_the_journey_reads_as_milestones_not_rows():
    found = detect(acme())
    assert titles(found) == [
        "Became a customer",
        "Started using Shopify",
        "First Shopify problem",
        "Health dropped 82 → 67",
        "Shopify problem reported 3 times",
        "Goal set: “Launch automation”",
        "Downgraded from Pro to Starter",
        "Said they may cancel",
        "Health dropped 67 → 48",
        "Lifecycle → At risk",
    ]
    assert found[0].what == "Their first recorded event: signup."
    # The first placement's automatic step is part of placing them, not a move.
    assert "Lifecycle → Active" not in titles(found)


def test_each_milestone_expands_into_the_five_answers():
    found = {milestone.title: milestone for milestone in detect(acme())}
    problem = found["First Shopify problem"]
    assert problem.what == "They reported: “The Shopify sync fails on large orders”."
    assert problem.why.startswith("Open problems pull health down until they are resolved; this one is still open after")
    assert [ref["id"] for ref in problem.memories] == ["m_problem"]
    assert problem.health == {
        "before": {"score": 82.0, "band": "healthy"},
        "after": {"score": 67.0, "band": "watch"},
        "delta": -15.0,
        "drivers": ["1 unresolved problem"],
        "snapshot_id": "s2",
    }
    assert problem.topics == ["Shopify"] and problem.tone == "negative"

    # The threat moved the lifecycle: the transition its event led to.
    threat = found["Said they may cancel"]
    assert [(move["before"], move["after"]) for move in threat.transitions] == [("active", "at_risk")]
    assert threat.transitions[0]["reasons"] == ["said they may cancel"]
    assert threat.health["delta"] == -19.0

    # Health and the lifecycle sit at the event that caused them, and say when they were recorded.
    move = found["Lifecycle → At risk"]
    assert move.at == on(23) and move.why == "Because said they may cancel."
    assert move.as_dict()["recorded_at"] == on(25, 11)
    assert [ref["id"] for ref in move.memories] == ["m_intent"], "which memories its event changed"
    drop = found["Health dropped 82 → 67"]
    assert drop.at == on(5) and drop.what == "From healthy to watch."

    repeat = found["Shopify problem reported 3 times"]
    assert repeat.at == on(8) and repeat.what == "The 3rd report of “The Shopify sync fails on large orders”."
    assert "escalating is recommended" in repeat.why

    use = found["Started using Shopify"]
    assert use.why == "In use since: 12 Shopify events, the latest on 20 Sep 2026."


def test_no_snapshot_means_no_change_only_once_snapshots_were_being_taken():
    early = memory("m0", "problem", "The export fails.", on(2), event="e0")
    late = memory("m1", "problem", "The import fails.", on(12), event="e1")
    found = {m.subject: m for m in detect(inputs(memories=[early, late], points=[point("s1", on(10), "e_other", 70, "watch")]))}
    assert "health_unchanged" not in found["m0"].detail, "processed before the first snapshot: not measured"
    assert found["m1"].detail["health_unchanged"] is True


def test_a_problem_says_what_became_of_it():
    resolution = memory("m_fix", "fact", "The Shopify sync works again.", on(15), event="e_fix", resolved=True,
                        supersedes="m_problem", entity_names=["Shopify"])
    problem = memory("m_problem", "problem", "The Shopify sync fails.", on(5), event="e_p", status="superseded",
                     superseded_by="m_fix", entity_names=["Shopify"])
    found = {m.title: m for m in detect(inputs(memories=[problem, resolution], related={"m_problem": problem, "m_fix": resolution}))}
    assert found["First Shopify problem"].why.endswith("this one was resolved on 15 Sep 2026, after 10 days.")
    fixed = found["Shopify problem resolved"]
    assert fixed.what == "“The Shopify sync works again”. It had been open for 10 days."
    assert fixed.tone == "positive" and fixed.before_id == "m_problem"


def test_repeats_count_from_the_first_report_and_never_past_the_evidence():
    problem = memory("m1", "problem", "Exports time out.", on(1), reports=5)
    versions = [(version(f"v{n}", on(1 + n), f"e{n}"), problem) for n in range(1, 6)]
    found = detect(inputs(memories=[problem], versions=versions))
    assert [m.title for m in found if m.kind == "repeated"] == ["Problem reported 3 times", "Problem reported 5 times"]
    assert [m.detail["reports"] for m in found if m.kind == "repeated"] == [3, 5]


def test_health_wobbles_are_one_moment_and_sharp_swings_count():
    points = [
        point("a", on(1), None, 82, "healthy"),
        point("b", on(2), None, 78, "watch"),
        point("c", on(3), None, 81, "healthy"),  # back within two days: a wobble
        point("d", on(10), None, 79, "watch"),
        point("e", on(12), None, 62, "watch"),  # 17 points down within the band
    ]
    found = [m for m in detect(inputs(points=points)) if m.category == "health"]
    assert titles(found) == ["Health dropped 81 → 79", "Health dropped 79 → 62"]
    assert found[1].what == "Down 17 points, still watch."


def test_silences_and_returns():
    left = NOW - timedelta(days=120)
    gaps = [Gap(left, "e1", left + timedelta(days=50), "e2", "login"), Gap(NOW - timedelta(days=40), "e3", None)]
    found = [m for m in detect(inputs(gaps=gaps)) if m.category == "activity"]
    assert [(m.title, m.at) for m in found] == [
        ("Went quiet", left + timedelta(days=30)),
        ("Came back after 7 weeks", left + timedelta(days=50)),
        ("Went quiet", NOW - timedelta(days=10)),
    ]
    # A silence not yet a month long is not one.
    assert detect(inputs(gaps=[Gap(NOW - timedelta(days=12), "e4", None)])) == []
    assert found[1].what == "Their first event in 7 weeks: login."
    assert found[2].what.startswith("No activity since") and found[2].detail["ongoing"] is True


def test_lifecycle_moves_on_every_track_and_passing_through_weighs_less():
    stays = [
        stay("t0", None, "new", on(1), track="engagement", source="initial"),
        stay("t1", "new", "activated", on(10), track="engagement"),
        stay("t2", "activated", "at_risk", on(10) + timedelta(milliseconds=3), track="engagement"),
        stay("t3", "at_risk", "active", on(12), source="manual"),
    ]
    stays[3].row.reason = "Spoke to them; the threat is withdrawn."
    found = {m.title: m for m in detect(inputs(stays=stays))}
    assert found["Engagement → Activated"].importance < found["Engagement → At risk"].importance
    assert found["Engagement → At risk"].tone == "negative"
    manual = found["Lifecycle → Active"]
    assert manual.what == "From at risk to active — set by a person." and manual.tone == "positive"
    assert manual.why == "Because Spoke to them; the threat is withdrawn."


def test_a_burst_of_events_is_not_mistaken_for_the_first_placement():
    """An import processes many events a second. Only the refresh that placed them — the one
    sharing its snapshot — is placement; the next refresh's move, a millisecond later, is news."""
    stays = [
        stay("p0", None, "onboarding", on(3), source="initial", snapshot="s1"),
        stay("p1", "onboarding", "active", on(3) + timedelta(milliseconds=1), snapshot="s1"),
        stay("p2", "active", "at_risk", on(3) + timedelta(milliseconds=400), snapshot="s2"),
    ]
    found = detect(inputs(stays=stays, points=[point("s1", on(3), "e1", 70, "watch"), point("s2", on(3) + timedelta(milliseconds=401), "e2", 50, "at_risk")]))
    assert [m.title for m in found if m.category == "lifecycle"] == ["Lifecycle → At risk"]
    first = next(m for m in found if m.category == "health")
    assert first.transitions and first.transitions[0]["after"] == "at_risk"


def test_a_topic_is_a_product_integration_or_feature():
    """Extraction names "The CSV" (a phrase) and "ts-support" (a channel filed as a person)."""
    first = memory("m1", "problem", "The CSV importer times out.", on(3), entity_names=["The CSV"])
    second = memory("m2", "problem", "The Shopify sync fails, says ts-support.", on(4), entity_names=["ts-support", "Shopify"])
    types = {"the csv": "other", "ts-support": "employee", "shopify": "integration"}
    found = detect(inputs(memories=[first, second], topic_types=types))
    assert titles(found) == ["First problem", "First Shopify problem"]
    assert found[1].topics == ["Shopify"]


def test_goals_are_named_as_a_person_would():
    goal = SimpleNamespace(
        id="g2", statement="The customer's goal is launch automation.", memory_id=None, opened_at=on(2),
        last_signal_at=on(2), status="open", evidence=[],
    )
    assert titles(detect(inputs(goals=[goal]))) == ["Goal set: “Launch automation”"]


def test_scores_speak_through_their_band_and_words_through_their_sentiment():
    detractor = memory("f1", "feedback", "The customer gave a satisfaction score of 4 (detractor).", on(3))
    promoter = memory("f2", "feedback", "The customer gave a satisfaction score of 9 (promoter).", on(4), band="promoter")
    passive = memory("f3", "feedback", "The customer gave a satisfaction score of 7 (passive).", on(5))
    furious = memory("f4", "feedback", "The customer hates the new editor.", on(6))
    found = [m for m in detect(inputs(memories=[detractor, promoter, passive, furious])) if m.category == "feedback"]
    assert [(m.title, m.subject) for m in found] == [("Negative feedback", "f1"), ("Praise", "f2"), ("Negative feedback", "f4")]
    assert found[0].detail["band"] == "detractor"


def test_opt_outs_channels_and_changed_preferences():
    email = memory("p1", "preference", "The customer prefers email.", on(2))
    whatsapp = memory("p2", "preference", "The customer prefers WhatsApp instead of email.", on(9), supersedes="p1")
    no_calls = memory("p3", "preference", "Please do not call us.", on(10))
    found = detect(inputs(memories=[email, whatsapp, no_calls], related={"p1": email}))
    assert titles(found) == ["Prefers email", "Preferred channel changed to WhatsApp", "Asked not to be called"]
    changed = found[1]
    assert changed.what == "Now: “The customer prefers WhatsApp instead of email” (was: “The customer prefers email”)."
    assert changed.before_id == "p1"


def test_goals_set_reached_and_stalled_once_each():
    goal = SimpleNamespace(
        id="g1", statement="Roll out to finance", memory_id=None, opened_at=on(2), last_signal_at=on(20), status="stalled",
        evidence=[
            {"kind": "stated", "at": on(2).isoformat()},
            {"kind": "progress", "at": on(5).isoformat()},
            {"kind": "stalled", "idle_days": 35, "at": on(20).isoformat()},
            {"kind": "stalled", "idle_days": 40, "at": on(24).isoformat()},
        ],
    )
    found = detect(inputs(goals=[goal]))
    assert titles(found) == ["Goal set: “Roll out to finance”", "Goal stalled: “Roll out to finance”"]
    assert found[1].what == "No progress in 5 weeks." and found[1].subject == "g1"


def test_plans_read_like_the_changes_they_are():
    started = memory("s1", "subscription", "The customer started on the Pro plan.", on(1), plan="pro", direction="started")
    upgraded = memory("s2", "subscription", "The customer upgraded from the Pro plan to the Enterprise plan.", on(9),
                      plan="enterprise", previous_plan="pro", direction="upgraded")
    cancelled = memory("s3", "subscription", "The customer cancelled their subscription (Enterprise plan).", on(20),
                       plan="enterprise", direction="cancelled")
    found = detect(inputs(memories=[started, upgraded, cancelled]))
    assert titles(found) == ["Started on the Pro plan", "Upgraded from Pro to Enterprise", "Cancelled the Enterprise plan"]
    assert [m.tone for m in found] == ["neutral", "positive", "negative"]
    assert found[2].importance == 1.0


# ------------------------------------------------------------------ readers


def test_a_reader_is_not_shown_what_it_may_not_read():
    found = detect(acme())
    assert {"m_problem", "m_intent", "goal_1"} <= cited_ids(found)
    shown, withheld = shape(found, {"m_problem"})
    assert withheld == 2, "the first report and the repeats are both about it"
    assert "First Shopify problem" not in titles(shown)
    move = next(m for m in shown if m.category == "lifecycle")
    assert "m_problem" not in move.evidence.get("memories", [])

    email = memory("p1", "preference", "The customer prefers email about salary.", on(2))
    whatsapp = memory("p2", "preference", "The customer prefers WhatsApp instead of email.", on(9), supersedes="p1")
    changed = [m for m in detect(inputs(memories=[email, whatsapp], related={"p1": email})) if m.kind == "changed"]
    masked, _ = shape(changed, {"p1"})
    assert masked[0].what == "Now: “The customer prefers WhatsApp instead of email”."
    assert [ref["id"] for ref in masked[0].memories] == ["p2"]


# ------------------------------------------------------------------ present


def test_selection_keeps_the_most_important_in_the_order_they_happened():
    found = detect(acme())
    chosen, qualified = select(found, limit=4)
    assert qualified == len(found)
    assert titles(chosen) == [
        "Downgraded from Pro to Starter", "Said they may cancel", "Health dropped 67 → 48", "Lifecycle → At risk",
    ], "the weightiest four — a fall into at risk outranks one into watch — in the order they happened"
    important, count = select(found, limit=100, min_importance=0.85)
    assert count == len(important) and all(m.importance >= 0.85 for m in important)


def test_ids_are_stable_and_distinct():
    first, second = detect(acme()), detect(acme())
    assert [m.id for m in first] == [m.id for m in second]
    assert len({m.id for m in first}) == len(first)


def test_the_summary_and_the_page():
    found = detect(acme())
    text = summarise(found, lead="Customer since 1 Sep 2026")
    assert text.startswith("Customer since 1 Sep 2026 · 10 milestones: first Shopify problem (5 Sep 2026); ")
    assert summarise([], lead="Since 1 Sep 2026") == "Since 1 Sep 2026: nothing that stands out yet."
    page = markdown({"customer": {"name": "Acme"}, "summary": text, "milestones": [m.as_dict() for m in found]})
    assert page.startswith("# Acme — journey\n")
    assert "\n## September 2026\n" in page
    assert "- **5 Sep** — First Shopify problem. They reported: “The Shopify sync fails on large orders”." in page
    assert "Health 67 → 48. Lifecycle: active → at risk." in page
