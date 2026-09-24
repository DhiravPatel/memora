"""Guardrails: what an agent may do to a customer, decided with reasons and evidence."""

from __future__ import annotations

import pytest

from memory_engine.facts import CustomerFacts
from memory_engine.guardrails import (
    ALLOW,
    DENY,
    REQUIRE_APPROVAL,
    GuardrailError,
    Profile,
    check,
    compile_guardrails,
)


def facts(**values) -> CustomerFacts:
    evidence = values.pop("_evidence", {})
    by_value = values.pop("_by_value", {})
    restricted = values.pop("_restricted", set())
    document = CustomerFacts(
        values={key.replace("__", "."): value for key, value in values.items()},
        evidence=evidence,
        evidence_by_value=by_value,
    )
    document.restricted_ids = set(restricted)
    return document


HEALTHY = {"problems__open_count": 0, "health__band": "healthy", "intents__kinds": [], "state__current": "active"}


def decide(action: str, request: dict | None = None, **overrides):
    return check(action=action, request=request or {}, facts=facts(**{**HEALTHY, **overrides}))


# ------------------------------------------------------------------ built-ins


def test_a_healthy_customer_can_be_offered_an_upgrade():
    verdict = decide("offer_upgrade")
    assert verdict.decision == ALLOW and verdict.reasons == []


def test_an_open_problem_blocks_an_upsell_and_cites_it():
    verdict = decide("offer_upgrade", problems__open_count=2, _evidence={"problems.open_count": ["mem_1", "mem_2"]})
    assert verdict.decision == DENY
    assert verdict.reasons[0].rule == "open_problem_blocks_selling"
    assert "2 open problems" in verdict.reasons[0].explanation
    assert verdict.evidence == ["mem_1", "mem_2"]


def test_every_reason_is_reported_not_just_the_first():
    """An agent should learn every objection at once, not one retry at a time."""
    verdict = decide("offer_upgrade", problems__open_count=1, health__band="critical", intents__kinds=["cancellation"])
    assert {reason.rule for reason in verdict.reasons} == {
        "open_problem_blocks_selling",
        "at_risk_blocks_selling",
        "churn_intent_blocks_promotion",
    }


def test_the_preferred_channel_is_honoured():
    verdict = decide("contact_customer", {"channel": "email"}, preferences__channel="whatsapp")
    assert verdict.decision == DENY
    assert verdict.reasons[0].explanation == "The customer prefers whatsapp, not email."


def test_the_preferred_channel_matches_regardless_of_spelling():
    assert decide("contact_customer", {"channel": "WhatsApp"}, preferences__channel="whatsapp").decision == ALLOW


def test_no_preference_means_any_channel():
    assert decide("contact_customer", {"channel": "email"}, preferences__channel=None).decision == ALLOW


def test_closing_a_ticket_whose_problem_is_open_is_denied():
    verdict = decide(
        "close_ticket",
        {"topic": "shopify sync failures"},
        problems__open_count=1,
        _by_value={"problems.terms": {"sync": ["mem_1"], "fail": ["mem_1"]}},
    )
    assert verdict.decision == DENY
    assert verdict.evidence == ["mem_1"]


def test_closing_without_saying_which_problem_needs_a_person():
    verdict = decide("close_ticket", problems__open_count=1)
    assert verdict.decision == REQUIRE_APPROVAL


def test_money_always_needs_a_person():
    verdict = decide("offer_discount", {"amount": 20})
    assert verdict.decision == REQUIRE_APPROVAL
    assert "20" in verdict.reasons[0].explanation


def test_deny_beats_require_approval():
    verdict = decide("offer_discount", problems__open_count=1)
    # A discount is money (approval) — and not selling, so an open problem does not deny it.
    assert verdict.decision == REQUIRE_APPROVAL
    upsell = decide("upsell", problems__open_count=1)
    assert upsell.decision == DENY


def test_an_unknown_action_is_judged_only_by_profile_and_project_rules():
    assert decide("water_the_plants", problems__open_count=3).decision == ALLOW


# ------------------------------------------------------------------- profile


def test_a_profile_denies_what_it_never_allows():
    verdict = check(
        action="offer_discount",
        request={},
        facts=facts(**HEALTHY),
        profile=Profile(name="support-agent", denied_actions=frozenset({"offer_discount"})),
    )
    assert verdict.decision == DENY
    assert verdict.reasons[0].source == "profile"


def test_a_profile_allowlist_denies_everything_else():
    verdict = check(
        action="send_marketing",
        request={},
        facts=facts(**HEALTHY),
        profile=Profile(name="support-agent", allowed_actions=frozenset({"create_ticket", "send_email"})),
    )
    assert verdict.decision == DENY
    assert "may only take" in verdict.reasons[0].explanation


# -------------------------------------------------------------- project rules


def test_a_project_rule_reads_the_proposed_action():
    guardrails = compile_guardrails(
        {
            "rules": [
                {
                    "name": "big_refunds",
                    "actions": ["process_refund"],
                    "when": "request.amount > 500",
                    "decision": "deny",
                    "message": "Refunds over 500 go through finance.",
                }
            ]
        }
    )
    big = check(action="process_refund", request={"amount": 900}, facts=facts(**HEALTHY), guardrails=guardrails)
    small = check(action="process_refund", request={"amount": 50}, facts=facts(**HEALTHY), guardrails=guardrails)
    assert big.decision == DENY and big.reasons[0].explanation == "Refunds over 500 go through finance."
    assert small.decision == REQUIRE_APPROVAL, "the built-in money rule still applies"


def test_a_builtin_can_be_switched_off():
    guardrails = compile_guardrails({"disabled": ["money_requires_approval"]})
    verdict = check(action="offer_discount", request={}, facts=facts(**HEALTHY), guardrails=guardrails)
    assert verdict.decision == ALLOW


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"disabled": ["no_such_rule"]}, "Unknown built-in"),
        ({"rules": [{"when": "health.scor < 1"}]}, "health.score"),
        ({"rules": [{"when": "health.score < 1", "decision": "maybe"}]}, "deny"),
        ({"rules": [{"name": "channel_preference", "when": "health.score < 1"}]}, "already used"),
        ({"rules": [{"name": "x"}]}, "needs a 'when'"),
    ],
)
def test_a_broken_configuration_is_refused(raw, message):
    with pytest.raises(GuardrailError, match=message):
        compile_guardrails(raw)


# ----------------------------------------------------------------- clearance


def test_a_reader_without_clearance_is_denied_without_learning_the_secret():
    """The decision uses everything; the explanation quotes only what the caller may see."""
    full = facts(**HEALTHY, preferences__channel="whatsapp", _evidence={"preferences.channel": ["mem_secret"]}, _restricted={"mem_secret"})
    visible = facts(**HEALTHY, preferences__channel=None)
    verdict = check(action="contact_customer", request={"channel": "email"}, facts=full, visible=visible)
    assert verdict.decision == DENY
    assert "whatsapp" not in verdict.reasons[0].explanation.lower()
    assert "does not allow email" in verdict.reasons[0].explanation
