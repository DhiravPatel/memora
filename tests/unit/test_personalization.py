"""Personalization, as pure rules (§26 6.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from memory_engine.facts import CustomerFacts
from memory_engine.personalization import (
    HINT_DEFAULTS,
    PersonalizationError,
    PersonalizationInputs,
    Usage,
    canonical,
    changes,
    compile_rules,
    compute,
    describe,
    fingerprint,
    slug,
    stale,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def facts(**values) -> CustomerFacts:
    return CustomerFacts(values={key.replace("__", "."): value for key, value in values.items()})


def problem(ident: str, content: str, *, days: float = 2, reports: int = 1, **meta):
    return SimpleNamespace(
        id=ident, content=content, first_seen_at=ago(days), last_seen_at=ago(days / 2), evidence_count=reports, meta=meta
    )


def goal(ident: str, statement: str, status: str, *, days: float = 3, progress: float = 0.0):
    return SimpleNamespace(
        id=ident, statement=statement, status=status, last_signal_at=ago(days), progress=progress, memory_id=f"m_{ident}"
    )


ALLOW = {"decision": "allow", "summary": "Allowed: no rule objects."}
DENY_UPSELL = {"decision": "deny", "summary": "The customer has 1 open problem; resolve it before selling."}


def inputs(values: dict | None = None, *, rules: dict | None = None, guardrails: dict | None = None, **overrides):
    base = {
        "customer.age_days": 90,
        "activity.distinct_features": 2,
        "problems.open_count": 0,
        "problems.recent_count": 0,
        "problems.max_repeats": 0,
        "feedback.recent_negative_count": 0,
        "intents.kinds": [],
        "signals.opportunities": [],
        "health.band": "healthy",
        "health.score": 84.0,
    }
    return PersonalizationInputs(
        now=NOW,
        facts=facts(**{key.replace(".", "__"): value for key, value in {**base, **(values or {})}.items()}),
        rules=compile_rules(rules),
        guardrails=guardrails
        if guardrails is not None
        else {"offer_upgrade": ALLOW, "send_marketing": ALLOW, "request_review": ALLOW},
        **overrides,
    )


# ------------------------------------------------------------------ the rules


def test_the_defaults_compile_and_name_the_guardrail_actions_they_read():
    rules = compile_rules(None)
    assert [rule.value for rule in rules.experience] == ["new", "advanced", "intermediate", "beginner"]
    assert [rule.value for rule in rules.mood] == ["frustrated", "happy", "neutral"]
    assert [hint.key for hint in rules.hints] == list(HINT_DEFAULTS)
    assert rules.guardrail_actions() == ["offer_upgrade", "send_marketing", "request_review"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"hints": {"x": {"when": "health.score > 1"}}}, "not a valid hint name"),
        ({"hints": {"beta": {"when": "plan.name == 1"}}}, "Hint 'beta': Unknown fact 'plan.name'"),
        ({"hints": {"beta": {}}}, "Hint 'beta' needs a condition"),
        ({"experience": [{"level": "new", "when": None}, {"level": "old", "when": None}]}, "only the last rule may"),
        ({"experience": [{"level": "1st time", "when": None}]}, "not a valid name"),
        ({"mood": [{"mood": "calm", "when": "health.score > 1"}, {"mood": "calm", "when": None}]}, "listed twice"),
        ({"relied_on_uses": 0}, "'relied_on_uses'"),
        ({"hints": {f"hint_{n}": {"when": "health.score > 1"} for n in range(21)}}, "At most 20"),
        ({"hints": {"suppress_upsell": {"when": 'guardrail.offer_upgrade == "maybe"'}}}, "'maybe' is not a value"),
    ],
)
def test_bad_rules_are_refused_with_a_reason(raw, message):
    with pytest.raises(PersonalizationError) as caught:
        compile_rules(raw)
    assert message in str(caught.value)


def test_only_what_differs_from_the_defaults_is_stored():
    raw = {
        "experience": [dict(item) for item in describe(compile_rules(None))["defaults"]["experience"]],
        "hints": {
            "offer_help": {"enabled": False},
            "suppress_upsell": {"when": 'guardrail.offer_upgrade != "allow"'},
            "beta_invite": {"when": 'personalization.experience == "advanced"', "description": "Invite to the beta."},
        },
        "relied_on_uses": 3,
        "relied_on_days": 14,
    }
    assert canonical(raw) == {
        "hints": {
            "offer_help": {"enabled": False},
            "beta_invite": {"when": 'personalization.experience == "advanced"', "description": "Invite to the beta."},
        },
        "relied_on_days": 14,
    }
    rules = compile_rules(canonical(raw))
    assert "offer_help" not in {hint.key for hint in rules.hints}
    assert next(hint for hint in rules.hints if hint.key == "beta_invite").custom is True


# ------------------------------------------------------------------ compute


@pytest.mark.parametrize(
    ("values", "level"),
    [
        ({"customer.age_days": 5, "activity.distinct_features": 1}, "new"),
        ({"activity.distinct_features": 6}, "advanced"),
        ({"activity.distinct_features": 2, "lifecycle.engagement": "power_user"}, "advanced"),
        ({"activity.distinct_features": 3}, "intermediate"),
        ({"activity.distinct_features": 1}, "beginner"),
    ],
)
def test_experience_is_the_first_level_that_holds(values, level):
    document = compute(inputs(values))
    assert document["experience"] == level
    assert document["details"]["experience"]["value"] == level


def test_mood_reads_feedback_threats_and_advocacy():
    frustrated = compute(inputs({"feedback.recent_negative_count": 2}))
    assert frustrated["mood"] == "frustrated"
    assert frustrated["details"]["mood"]["because"] == ["2 negative pieces of feedback in 14 days"]
    assert compute(inputs({"intents.kinds": ["cancellation"]}))["mood"] == "frustrated"
    assert compute(inputs({"signals.opportunities": ["advocacy"]}))["mood"] == "happy"
    neutral = compute(inputs())
    assert neutral["mood"] == "neutral" and neutral["details"]["mood"]["rule"] is None


def test_hints_follow_the_guardrails_own_verdicts():
    document = compute(
        inputs(
            {"problems.open_count": 1, "problems.recent_count": 1},
            guardrails={"offer_upgrade": DENY_UPSELL, "send_marketing": ALLOW, "request_review": ALLOW},
        )
    )
    assert document["ui"]["suppress_upsell"] is True
    assert document["details"]["ui"]["suppress_upsell"]["because"] == [
        "The customer has 1 open problem; resolve it before selling."
    ]
    assert document["ui"]["suppress_marketing"] is False
    assert document["details"]["ui"]["suppress_marketing"]["because"] == ["send marketing is allowed"]
    assert document["ui"]["offer_help"] is True


def test_asking_for_a_review_needs_a_happy_customer_the_guardrails_let_you_ask():
    happy = inputs({"signals.opportunities": ["advocacy"]})
    assert compute(happy)["ui"]["ask_for_review"] is True
    refused = inputs(
        {"signals.opportunities": ["advocacy"]},
        guardrails={"offer_upgrade": ALLOW, "send_marketing": ALLOW, "request_review": {"decision": "deny", "summary": "No."}},
    )
    assert compute(refused)["ui"]["ask_for_review"] is False


def test_a_hint_reading_an_unknown_fact_is_off_and_says_so():
    document = compute(
        inputs(rules={"hints": {"vip_banner": {"when": 'customer.metadata.tier == "vip"'}}})
    )
    hint = document["details"]["ui"]["vip_banner"]
    assert document["ui"]["vip_banner"] is False and hint["known"] is False and hint["custom"] is True


def test_frictions_are_stable_keys_by_topic_or_area():
    problems = [
        problem("p1", "The Shopify sync fails on large orders.", reports=3, entity_names=["Shopify"]),
        problem("p2", "Shopify orders arrive late.", days=1, entity_names=["Shopify"]),
        problem("p3", "The payment failed for invoice 42.", days=4),
        problem("p4", "The dashboard is painfully slow.", days=6),
        problem("p5", "The CSV importer times out.", days=8, entity_names=["The CSV"]),
        problem("p6", "It was fixed.", days=1, resolved=True),
    ]
    document = compute(
        inputs(problems=problems, topic_types={"shopify": "integration", "the csv": "other"}, customer_name="Acme")
    )
    assert document["known_frictions"] == ["shopify", "billing", "reporting", "data_export"]
    shopify = document["details"]["known_frictions"][0]
    assert (shopify["label"], shopify["mode"], shopify["problems"], shopify["reports"]) == ("Shopify", "broken", 2, 4)
    assert shopify["memory_ids"] == ["p1", "p2"]
    modes = {item["key"]: item["mode"] for item in document["details"]["known_frictions"]}
    assert modes["billing"] == "broken" and modes["reporting"] == "slow" and modes["data_export"] == "slow"


def test_the_current_goal_is_the_one_moving():
    goals = [
        goal("g1", "The customer wants to migrate their contact list.", "stalled", days=1),
        goal("g2", "The customer's goal is launch automation.", "progressing", days=9, progress=0.4),
        goal("g3", "Roll out to finance", "open", days=2),
    ]
    document = compute(inputs(goals=goals))
    assert document["current_goal"] == "launch_automation"
    assert document["details"]["current_goal"] == {
        "id": "g2", "key": "launch_automation", "label": "Launch automation", "status": "progressing",
        "progress": 0.4, "memory_id": "m_g2",
    }
    assert compute(inputs(goals=[goal("g4", "Done", "achieved")]))["current_goal"] is None


def test_features_used_and_relied_on():
    uses = [
        Usage("feature", "Campaign Builder", ago(60), ago(1), 40, 9, "e1"),
        Usage("integration", "Shopify", ago(50), ago(20), 3, 0, "e2"),
        Usage("feature", "Reports", ago(30), ago(2), 4, 2, "e3"),
    ]
    document = compute(inputs(uses=uses))
    assert document["features_used"] == ["campaign_builder", "reports", "shopify"]
    assert document["relied_on_features"] == ["campaign_builder"]
    stricter = compute(inputs(uses=uses, rules={"relied_on_uses": 10}))
    assert stricter["relied_on_features"] == []


def test_channel_stage_plan_and_health_come_from_the_facts():
    document = compute(
        inputs(
            {
                "preferences.channel": "whatsapp",
                "preferences.channel_outdated": True,
                "preferences.observed_channel": "email",
                "preferences.opt_outs": ["phone"],
                "state.current": "at_risk",
                "lifecycle.engagement": "adopting",
                "subscription.plan": "pro",
            }
        )
    )
    assert (document["preferred_channel"], document["opt_outs"], document["stage"], document["plan"], document["health"]) == (
        "whatsapp", ["phone"], "at_risk", "pro", "healthy",
    )
    assert document["details"]["preferred_channel"]["outdated"] is True
    assert document["details"]["stage"] == {"lifecycle": "at_risk", "engagement": "adopting"}


# ------------------------------------------------------------------ versions


def test_the_version_follows_what_the_product_sees_and_changes_say_what_moved():
    first = compute(inputs())
    assert fingerprint(first) == fingerprint(compute(inputs())), "same inputs, same version"
    second = compute(inputs({"problems.open_count": 1, "problems.recent_count": 1}, problems=[problem("p1", "The export fails.")]))
    assert fingerprint(first) != fingerprint(second)
    assert changes(first, second) == [
        {"field": "known_frictions", "before": [], "after": ["data_export"]},
        {"field": "ui.offer_help", "before": False, "after": True},
    ]
    assert changes(None, second) == []


def test_slugs_and_staleness():
    assert slug("Launch automation — by Q4!") == "launch_automation_by_q4"
    assert slug("Campaign Builder") == "campaign_builder"
    assert stale(None, NOW) and stale(ago(2), NOW) and not stale(ago(0.5), NOW)
