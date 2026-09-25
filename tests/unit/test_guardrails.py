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


def test_a_preference_drift_says_otherwise_is_still_honoured_and_named():
    """Never silently changed (§26 5.5): the stated channel decides; the agent is told."""
    verdict = decide(
        "contact_customer",
        {"channel": "whatsapp"},
        preferences__channel="email",
        preferences__channel_outdated=True,
        preferences__observed_channel="whatsapp",
        preferences__observed_share=0.857,
    )
    assert verdict.decision == DENY
    assert verdict.reasons[0].explanation == (
        "The customer prefers email, not whatsapp — though 86% of their contacts since came through "
        "WhatsApp; a person can confirm the change."
    )


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


# ------------------------------------------------------------ opt-outs (§26 4.5)


def _opted(*kinds: str, **extra):
    return {
        "preferences__opt_outs": list(kinds),
        "_by_value": {"preferences.opt_outs": {kind: [f"mem_{kind}"] for kind in kinds}},
        **extra,
    }


def test_a_customer_who_asked_not_to_be_called_is_not_called():
    verdict = decide("call_customer", **_opted("phone"))
    assert verdict.decision == DENY
    reason = verdict.reasons[0]
    assert (reason.rule, reason.explanation) == ("respect_opt_out", "The customer asked not to be called.")
    assert reason.evidence == ["mem_phone"]
    # Email was never refused.
    assert decide("send_email", **_opted("phone")).decision == ALLOW
    # A channel named in the request counts the same as the action's own.
    assert decide("contact_customer", {"channel": "Telephone"}, **_opted("phone")).decision == DENY


def test_do_not_contact_stops_outreach_but_not_a_reply():
    assert decide("contact_customer", **_opted("contact")).decision == DENY
    assert decide("send_message", {"reply": True}, **_opted("contact")).decision == ALLOW
    # A reply by a channel they refused is still refused.
    assert decide("send_message", {"reply": True, "channel": "sms"}, **_opted("contact", "sms")).decision == DENY


def test_sales_and_marketing_opt_outs():
    assert decide("offer_upgrade", **_opted("sales")).reasons[0].explanation == "The customer asked for no sales outreach."
    assert decide("send_marketing", **_opted("marketing")).decision == DENY
    assert decide("send_marketing", **_opted("sales")).decision == ALLOW
    assert decide("close_ticket", {"topic": "export"}, **_opted("contact", "sales", "marketing")).decision == ALLOW


def test_an_opt_out_the_reader_may_not_see_is_explained_without_it():
    full = facts(**{**HEALTHY, **_opted("phone")})
    shown = full.redacted({"mem_phone"})
    verdict = check(action="call_customer", request={}, facts=full, visible=shown)
    assert verdict.decision == DENY
    assert verdict.reasons[0].explanation == "The customer's contact preferences do not allow this."
    assert verdict.reasons[0].evidence == []


def test_the_opt_out_rule_can_be_switched_off():
    config = compile_guardrails({"disabled": ["respect_opt_out"]})
    verdict = check(action="call_customer", request={}, facts=facts(**{**HEALTHY, **_opted("phone")}), guardrails=config)
    assert verdict.decision == ALLOW


# ------------------------------------------------------- limits and history


LIMITS = compile_guardrails({"auto_approve": [{"actions": ["process_refund"], "up_to": 50, "max_per_30_days": 3}]})


def test_a_refund_under_the_limit_needs_nobody():
    verdict = check(action="process_refund", request={"amount": 25}, facts=facts(**HEALTHY), guardrails=LIMITS)
    assert verdict.decision == ALLOW
    assert verdict.reasons[0].rule == "auto_approved"
    assert verdict.reasons[0].explanation == (
        "Approved automatically: a refund of 25 within the limit of 50 (1 of 3 this month), set by the project."
    )


def test_over_the_amount_or_the_monthly_cap_a_person_decides():
    over = check(action="process_refund", request={"amount": 80}, facts=facts(**HEALTHY), guardrails=LIMITS)
    assert over.decision == REQUIRE_APPROVAL
    assert "over the project's automatic limit of 50" in over.reasons[0].explanation
    capped = check(
        action="process_refund",
        request={"amount": 10},
        facts=facts(**{**HEALTHY, "actions__process_refund__count_30d": 3}),
        guardrails=LIMITS,
    )
    assert capped.decision == REQUIRE_APPROVAL
    assert "already reach its monthly cap of 3" in capped.reasons[0].explanation
    # Other money actions are not covered by the refund limit.
    credit = check(action="issue_credit", request={"amount": 5}, facts=facts(**HEALTHY), guardrails=LIMITS)
    assert credit.decision == REQUIRE_APPROVAL


def test_a_project_rule_is_not_lifted_by_a_limit():
    config = compile_guardrails(
        {
            "auto_approve": [{"actions": ["issue_credit"], "up_to": 100}],
            "rules": [
                {
                    "name": "third_credit",
                    "actions": ["issue_credit"],
                    "when": "actions.issue_credit.count_30d >= 2",
                    "decision": "require_approval",
                    "message": "A third credit this month needs a person.",
                }
            ],
        }
    )
    first = check(action="issue_credit", request={"amount": 20}, facts=facts(**HEALTHY), guardrails=config)
    assert first.decision == ALLOW
    third = check(
        action="issue_credit",
        request={"amount": 20},
        facts=facts(**{**HEALTHY, "actions__issue_credit__count_30d": 2}),
        guardrails=config,
    )
    assert third.decision == REQUIRE_APPROVAL
    assert third.summary() == "A third credit this month needs a person."


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"auto_approve": [{"actions": ["offer_upgrade"], "up_to": 5}]}, "never needs approval"),
        ({"auto_approve": [{"actions": ["process_refund"]}]}, "need an 'up_to'"),
        ({"auto_approve": [{"actions": ["process_refund"], "up_to": -1}]}, "positive number"),
        ({"auto_approve": [{"actions": ["process_refund"], "up_to": 5, "max_per_30_days": 0}]}, "at least 1"),
        ({"auto_approve": "yes"}, "must be a list"),
    ],
)
def test_a_limit_that_makes_no_sense_is_refused(raw, message):
    with pytest.raises(GuardrailError, match=message):
        compile_guardrails(raw)
