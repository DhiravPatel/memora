"""The lifecycle machine and what counts as a material change."""

from __future__ import annotations

import pytest

from memory_engine.facts import CustomerFacts
from memory_engine.lifecycle import MAX_HOPS, LifecycleError, compile_lifecycle
from memory_engine.reasons import reasons
from memory_engine.snapshots import changes, fingerprint


def facts(**values) -> CustomerFacts:
    return CustomerFacts(values={key.replace("__", "."): value for key, value in values.items()})


DEFAULT = compile_lifecycle(None)


# ----------------------------------------------------------------- the default


def test_a_brand_new_customer_starts_in_the_initial_state():
    assert DEFAULT.settle(None, facts()) == []
    assert DEFAULT.initial == "onboarding"


def test_cancellation_language_puts_a_customer_at_risk():
    steps = DEFAULT.settle("active", facts(intents__kinds=["cancellation"]))
    assert [step.target for step in steps] == ["at_risk"]
    assert steps[0].transition.name == "at_risk"


def test_a_completed_cancellation_wins_over_being_at_risk():
    """Transitions are tried in order; churn is listed first because it is final."""
    steps = DEFAULT.settle(
        "active", facts(subscription__direction="cancelled", intents__kinds=["cancellation"])
    )
    assert steps[0].target == "churned"


def test_hysteresis_keeps_a_borderline_customer_from_flapping():
    """Churn risk 0.5: too low to *enter* at_risk (0.6), too high to *leave* it (0.4)."""
    borderline = facts(**{"health__band": "watch", "signals__churn_risk": 0.5, "intents__kinds": []})
    assert DEFAULT.settle("active", borderline) == []
    assert DEFAULT.settle("at_risk", borderline) == []


def test_recovery_needs_every_condition():
    recovered = facts(health__band="healthy", signals__churn_risk=0.2, intents__kinds=[])
    assert [step.target for step in DEFAULT.settle("at_risk", recovered)] == ["active"]


def test_why_now_is_what_still_holds_not_what_held_on_entry():
    """Entered at risk on a critical score; health recovered, the threat to cancel did not."""
    now = facts(health__band="watch", signals__churn_risk=0.19, intents__kinds=["cancellation", "evaluation"])
    standing = DEFAULT.standing("at_risk", now, entered_by="at_risk", came_from="active")
    assert standing is not None and standing.holds and standing.transition == "at_risk"
    assert reasons(standing.evaluation) == ["said they may cancel; is evaluating"]


def test_held_by_hysteresis_the_reasons_are_what_keeps_them():
    """Nothing that put them at risk holds, and the way out is stricter: say what blocks it."""
    borderline = facts(
        health__band="watch", signals__churn_risk=0.5, intents__kinds=[], subscription__direction="upgraded"
    )
    standing = DEFAULT.standing("at_risk", borderline, entered_by="at_risk", came_from="active")
    assert standing is not None and not standing.holds and not standing.moving
    assert standing.transition == "recovered", "the way out they are closest to"
    assert reasons(standing.evaluation) == ["forecast churn risk 0.50"]


def test_a_way_out_that_would_fire_means_they_are_moving():
    recovered = facts(health__band="healthy", signals__churn_risk=0.2, intents__kinds=[])
    standing = DEFAULT.standing("at_risk", recovered, entered_by="at_risk")
    assert standing is not None and standing.moving and standing.transition == "recovered"


def test_nothing_to_recheck_for_a_person_or_the_initial_state():
    assert DEFAULT.standing("at_risk", facts(), entered_by=None) is None
    assert DEFAULT.standing("at_risk", facts(), entered_by="no_such_transition") is None
    assert DEFAULT.standing("active", facts(), entered_by="at_risk") is None, "a transition that leads elsewhere"


def test_an_unknown_fact_never_moves_a_customer():
    """Three-valued logic: no plan recorded is not the same as 'not on trial'."""
    assert DEFAULT.settle("trial", facts(subscription__plan=None)) == []


def test_several_moves_can_happen_at_once_without_cycling():
    machine = compile_lifecycle(
        {
            "states": ["a", "b", "c"],
            "initial": "a",
            "transitions": [
                {"name": "ab", "from": "a", "to": "b", "when": "health.score >= 0"},
                {"name": "bc", "from": "b", "to": "c", "when": "health.score >= 0"},
                {"name": "ca", "from": "c", "to": "a", "when": "health.score >= 0"},
            ],
        }
    )
    steps = machine.settle("a", facts(health__score=50))
    assert [step.target for step in steps] == ["b", "c"], "stops before revisiting 'a'"
    assert len(steps) <= MAX_HOPS


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("machine", "message"),
    [
        ({"states": []}, "non-empty list of states"),
        ({"states": ["a", "a"]}, "unique"),
        ({"states": ["a b"]}, "not a valid state name"),
        ({"states": ["a"], "initial": "z"}, "initial state"),
        ({"states": ["a", "b"], "transitions": [{"to": "z", "when": "health.score < 1"}]}, "not a state"),
        ({"states": ["a", "b"], "transitions": [{"from": "q", "to": "b", "when": "health.score < 1"}]}, "not a state"),
        ({"states": ["a", "b"], "transitions": [{"to": "b"}]}, "needs a 'when'"),
        ({"states": ["a", "b"], "transitions": [{"to": "b", "when": "health.scor < 1"}]}, "health.score"),
        ({"states": ["a", "b"], "transitions": [{"from": "b", "to": "b", "when": "health.score < 1"}]}, "to itself"),
        (
            {"states": ["a", "b"], "transitions": [
                {"name": "x", "to": "b", "when": "health.score < 1"},
                {"name": "x", "to": "a", "when": "health.score < 1"},
            ]},
            "unique",
        ),
    ],
)
def test_a_broken_machine_is_refused_with_a_reason(machine, message):
    with pytest.raises(LifecycleError, match=message):
        compile_lifecycle(machine)


def test_a_machine_round_trips_through_its_stored_form():
    again = compile_lifecycle(DEFAULT.as_dict())
    assert again.as_dict() == DEFAULT.as_dict()


# ------------------------------------------------------------------- snapshots


def test_health_drift_within_a_bucket_is_not_material():
    """Memories decay a little every day; a snapshot per decimal point would be noise."""
    assert fingerprint(facts(health__score=61.2)) == fingerprint(facts(health__score=63.9))
    assert fingerprint(facts(health__score=61.2)) != fingerprint(facts(health__score=66.0))


def test_a_new_problem_is_material():
    assert fingerprint(facts(problems__open_count=1)) != fingerprint(facts(problems__open_count=2))


def test_intent_order_does_not_matter():
    assert fingerprint(facts(intents__kinds=["expansion", "cancellation"])) == fingerprint(
        facts(intents__kinds=["cancellation", "expansion"])
    )


def test_changes_report_before_after_and_list_deltas():
    previous = {"values": {"problems.entities": ["shopify"], "health.band": "watch"}}
    current = facts(problems__entities=["shopify", "stripe"], health__band="at_risk")
    found = {change["fact"]: change for change in changes(previous, current)}
    assert found["health.band"]["before"] == "watch" and found["health.band"]["after"] == "at_risk"
    assert found["problems.entities"]["added"] == ["stripe"]
    assert found["problems.entities"]["removed"] == []


def test_the_first_snapshot_reports_what_it_found():
    first = changes(None, facts(health__band="healthy", problems__open_count=0))
    assert [change["fact"] for change in first] == ["health.band"]
