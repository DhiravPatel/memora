"""The fact document: what each fact means, and where its value comes from.

The builder is pure, so these tests hand it plain objects and pin each family of facts —
including the ones whose derivation is a judgement call (which subscription statement is
current, which preference names the channel, when churn language filed as a problem counts
as intent).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from common.time import utcnow
from memory_engine.facts import CATALOG, FactInputs, build_facts
from nlp.intents import intent_kinds
from nlp.tokenize import root, surface_words

NOW = utcnow()


def memory(ident: str, content: str, *, days_ago: float = 1, first_days_ago: float | None = None, repeats: int = 1, **meta):
    return SimpleNamespace(
        id=ident,
        content=content,
        evidence_count=repeats,
        first_seen_at=NOW - timedelta(days=first_days_ago if first_days_ago is not None else days_ago),
        last_seen_at=NOW - timedelta(days=days_ago),
        meta=meta,
    )


def customer(**overrides):
    base = {
        "external_id": "cus_1",
        "name": "Acme",
        "email": "ops@acme.io",
        "created_at": NOW - timedelta(days=90),
        "last_event_at": NOW - timedelta(days=2),
        "meta": {"segment": "smb", "arr": 12000},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def build(**grouped):
    return build_facts(FactInputs(customer=customer(), now=NOW, memories_by_type=grouped))


# ------------------------------------------------------------------------- who


def test_the_customer_block_is_derived_from_the_customer_row():
    facts = build()
    assert facts.get("customer.external_id") == "cus_1"
    assert facts.get("customer.email_domain") == "acme.io"
    assert facts.get("customer.age_days") == pytest.approx(90, abs=0.01)
    assert facts.get("customer.metadata.segment") == "smb"
    assert facts.get("customer.metadata.missing") is None


# ------------------------------------------------------------------ subscription


def test_the_current_plan_is_the_newest_statement_not_the_loudest():
    facts = build(
        subscription=[
            memory("mem_old", "The customer downgraded from the Enterprise plan to the Starter plan.", days_ago=30),
            memory("mem_new", "The customer moved onto the Pro plan.", days_ago=2),
        ]
    )
    assert facts.get("subscription.plan") == "pro"
    assert facts.evidence["subscription.plan"] == ["mem_new"]


def test_structured_subscription_attributes_win_over_parsing():
    facts = build(
        subscription=[
            memory(
                "mem_1",
                "The customer upgraded from the Starter plan to the Pro plan",
                plan="Pro",
                previous_plan="Starter",
                direction="upgraded",
            )
        ]
    )
    assert facts.get("subscription.plan") == "pro"
    assert facts.get("subscription.previous_plan") == "starter"
    assert facts.get("subscription.direction") == "upgraded"


@pytest.mark.parametrize(
    ("text", "plan", "previous", "direction"),
    [
        ("The customer downgraded from the Pro plan to the Starter plan.", "starter", "pro", "downgraded"),
        ("The customer cancelled their subscription (Pro plan).", "pro", None, "cancelled"),
        ("The customer renewed on the Enterprise plan.", "enterprise", None, "renewed"),
    ],
)
def test_a_subscription_statement_in_prose_is_parsed(text, plan, previous, direction):
    facts = build(subscription=[memory("mem_1", text)])
    assert facts.get("subscription.plan") == plan
    assert facts.get("subscription.previous_plan") == previous
    assert facts.get("subscription.direction") == direction


def test_no_subscription_means_unknown_not_empty():
    facts = build()
    assert facts.get("subscription.plan") is None
    assert facts.get("subscription.direction") is None


# ---------------------------------------------------------------------- problems


def test_problems_report_entities_with_exactly_the_memories_that_mention_them():
    facts = build(
        problem=[
            memory("mem_1", "The Shopify sync fails at checkout.", entity_names=["Shopify"], first_days_ago=20, repeats=4),
            memory("mem_2", "Exports time out.", entity_names=["CSV export"], first_days_ago=3),
        ]
    )
    assert facts.get("problems.open_count") == 2
    assert facts.get("problems.entities") == ["csv export", "shopify"]
    assert facts.evidence_for("problems.entities", "Shopify") == ["mem_1"]
    assert facts.get("problems.oldest_open_days") == pytest.approx(20, abs=0.01)
    assert facts.get("problems.max_repeats") == 4


def test_problem_terms_are_readable_words_matched_by_family():
    facts = build(problem=[memory("mem_1", "The sync keeps failing and invoices are wrong.")])
    terms = facts.get("problems.terms")
    assert "failing" in terms and "invoices" in terms
    # Cited by family: a rule asking about "failures" finds the memory that said "failing".
    assert facts.evidence_for("problems.terms", "failures") == ["mem_1"]


# -------------------------------------------------------------------- preferences


def test_the_preferred_channel_is_the_newest_preference_naming_one():
    facts = build(
        preference=[
            memory("mem_old", "The customer prefers email.", days_ago=40, channels=["email"]),
            memory("mem_new", "Please reach the customer on WhatsApp.", days_ago=1, channels=["WhatsApp"]),
            memory("mem_other", "The customer likes weekly summaries.", days_ago=0.5),
        ]
    )
    assert facts.get("preferences.channel") == "whatsapp"
    assert facts.evidence["preferences.channel"] == ["mem_new"]
    assert facts.get("preferences.channels") == ["email", "whatsapp"]


def test_a_channel_is_found_even_without_stored_metadata():
    facts = build(preference=[memory("mem_1", "The customer prefers a phone call.")])
    assert facts.get("preferences.channel") == "phone"


# ------------------------------------------------------------------------ intents


def test_intents_are_classified_into_kinds():
    facts = build(intent=[memory("mem_1", "The customer wants to add seats for another team.")])
    assert facts.get("intents.kinds") == ["expansion"]
    assert facts.get("intents.latest_kind") == "expansion"


def test_churn_language_filed_as_a_problem_still_counts_as_intent():
    """"We will cancel if this is not fixed" is usually filed as a problem."""
    facts = build(problem=[memory("mem_1", "The customer will cancel if the sync is not fixed.")])
    assert "cancellation" in facts.get("intents.kinds")
    assert facts.evidence_for("intents.kinds", "cancellation") == ["mem_1"]


# ----------------------------------------------------------------------- goals


def test_goals_are_counted_by_status_with_ids_as_evidence():
    goals = [
        SimpleNamespace(id="goal_1", status="open", statement="Launch the Shopify integration"),
        SimpleNamespace(id="goal_2", status="stalled", statement="Migrate the reporting"),
        SimpleNamespace(id="goal_3", status="achieved", statement="Onboard the support team"),
    ]
    facts = build_facts(FactInputs(customer=customer(), now=NOW, goals=goals))
    assert facts.get("goals.open_count") == 1
    assert facts.get("goals.stalled_count") == 1
    assert facts.get("goals.achieved_count") == 1
    assert facts.evidence["goals.stalled_count"] == ["goal_2"]
    # Only live goals contribute words — an achieved goal is not what they are pursuing.
    assert "onboard" not in facts.get("goals.terms")


# --------------------------------------------------------------------- activity


@pytest.mark.parametrize(
    ("recent", "prior", "idle", "trend"),
    [(20, 10, 1, "growing"), (10, 10, 1, "steady"), (4, 10, 1, "declining"), (0, 10, 20, "silent")],
)
def test_activity_trend_uses_the_same_windows_as_the_forecast(recent, prior, idle, trend):
    report = SimpleNamespace(
        trajectory="steady", churn_risk=0.1, expansion_score=0.1, confidence=0.5,
        measurements={"events_recent": recent, "events_prior": prior},
        signals=[], risks=[], opportunities=[],
    )
    facts = build_facts(
        FactInputs(customer=customer(last_event_at=NOW - timedelta(days=idle)), now=NOW, report=report)
    )
    assert facts.get("activity.trend") == trend


def test_every_fact_the_builder_writes_is_in_the_catalog():
    """A fact rules cannot validate against is a fact nobody can use."""
    facts = build()
    written = {name for name in facts.values if name != "customer.metadata"}
    assert written <= set(CATALOG)


# ------------------------------------------------------------------ intents, words


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("We are going to cancel if this is not fixed.", ["cancellation"]),
        ("We are not planning to cancel.", []),
        ("Not happy, but we will renew the annual contract.", ["renewal"]),
        ("Can we add seats for another team?", ["expansion"]),
        ("We want to migrate from Zendesk.", ["migration"]),
        ("We need SSO before the rollout.", ["expansion", "integration"]),
    ],
)
def test_intent_kinds_respect_negation_and_clause_breaks(text, kinds):
    assert intent_kinds(text) == kinds


def test_surface_words_are_not_lemmatised():
    assert surface_words("The salaries are failing") == ["salaries", "failing"]


def test_root_folds_a_family_onto_one_word():
    assert {root(word) for word in ("fail", "failed", "failing", "failure", "failures")} == {"fail"}
    assert root("customer") != root("custom")


# --------------------------------------------------------------------- redaction


def restricted(ident: str, content: str, **meta):
    item = memory(ident, content, **meta)
    item.sensitivity = "restricted"
    return item


def test_redaction_removes_only_what_came_only_from_restricted_memories():
    facts = build(
        problem=[
            memory("mem_open", "The Shopify sync fails.", entity_names=["Shopify"]),
            restricted("mem_secret", "The HR payroll export leaks salaries.", entity_names=["HR"]),
        ]
    )
    assert set(facts.get("problems.entities")) == {"shopify", "hr"}

    view = facts.redacted()
    assert view.get("problems.entities") == ["shopify"]
    assert "salaries" not in view.get("problems.terms")
    assert "shopify" in view.get("problems.terms")
    assert "problems.entities" in view.withheld_facts
    # A count is a number about the customer, not a quote from one: it stays whole.
    assert view.get("problems.open_count") == 2
    # And no restricted id survives anywhere in the evidence.
    for ids in view.evidence.values():
        assert "mem_secret" not in ids


def test_a_value_shared_with_a_visible_memory_survives_redaction():
    """"Shopify" mentioned in both a restricted and an ordinary memory is not a secret."""
    facts = build(
        problem=[
            memory("mem_open", "Shopify orders are late.", entity_names=["Shopify"]),
            restricted("mem_secret", "Shopify refunds for the legal case.", entity_names=["Shopify"]),
        ]
    )
    view = facts.redacted()
    assert view.get("problems.entities") == ["shopify"]
    assert view.evidence_for("problems.entities", "shopify") == ["mem_open"]


def test_a_preference_known_only_from_a_restricted_memory_becomes_unknown():
    facts = build(preference=[restricted("mem_secret", "Only call their personal phone.", channels=["phone"])])
    assert facts.get("preferences.channel") == "phone"
    assert facts.redacted().get("preferences.channel") is None


def test_a_goal_born_from_a_restricted_memory_is_redacted_too():
    goals = [
        SimpleNamespace(id="goal_1", status="open", statement="Settle the salary dispute", memory_id="mem_secret"),
        SimpleNamespace(id="goal_2", status="open", statement="Launch the Shopify store", memory_id="mem_open"),
    ]
    facts = build_facts(
        FactInputs(customer=customer(), now=NOW, goals=goals, restricted_memory_ids=frozenset({"mem_secret"}))
    )
    view = facts.redacted()
    assert "salary" not in view.get("goals.terms")
    assert "shopify" in view.get("goals.terms")
    assert view.get("goals.open_count") == 2


def test_nothing_restricted_means_the_same_document():
    facts = build(problem=[memory("mem_1", "Exports time out.")])
    assert facts.redacted() is facts


# ------------------------------------------------------ threats are not churn


@pytest.mark.parametrize(
    ("text", "direction"),
    [
        ("The customer is going to cancel unless the Shopify sync is fixed this week.", None),
        ("The customer will downgrade if pricing goes up.", None),
        ("The customer plans to cancel next month.", None),
        ("The customer cancelled their subscription (Pro plan).", "cancelled"),
        ("The customer upgraded from the Starter plan to the Pro plan.", "upgraded"),
        ("The customer renewed on the Enterprise plan in May.", "renewed"),
    ],
)
def test_only_a_completed_change_is_a_direction(text, direction):
    """A threat to cancel read as a cancellation marks a recoverable customer as churned."""
    facts = build(subscription=[memory("mem_1", text)])
    assert facts.get("subscription.direction") == direction


def test_a_threat_filed_as_a_subscription_is_still_intent():
    facts = build(subscription=[memory("mem_1", "The customer is going to cancel unless the sync is fixed.")])
    assert facts.get("subscription.direction") is None
    assert "cancellation" in facts.get("intents.kinds")


def test_the_preferred_channel_is_read_for_stance_not_mention():
    # "WhatsApp instead of email" names email first in the lexicon, and prefers WhatsApp.
    facts = build(preference=[memory("mem_1", "Please contact the customer on WhatsApp instead of email.")])
    assert facts.get("preferences.channel") == "whatsapp"
    assert facts.get("preferences.channels") == ["email", "whatsapp"]

    # A newer statement that only turns a channel away rules out an older preference for it.
    facts = build(
        preference=[
            memory("mem_old", "The customer prefers email.", days_ago=20),
            memory("mem_new", "Stop emailing the customer.", days_ago=1),
        ]
    )
    assert facts.get("preferences.channel") is None
    assert facts.get("preferences.channels") == ["email"]

    # …but not one it did not mention.
    facts = build(
        preference=[
            memory("mem_old", "The customer prefers email.", days_ago=20),
            memory("mem_new", "Please do not call the customer.", days_ago=1),
        ]
    )
    assert facts.get("preferences.channel") == "email"
    assert facts.evidence["preferences.channel"] == ["mem_old"]


def test_a_change_that_happened_is_not_an_intent():
    facts = build(
        subscription=[
            memory("mem_done", "The customer downgraded from the Pro plan to the Starter plan, citing: the integration never worked.", direction="downgraded"),
        ]
    )
    assert facts.get("subscription.direction") == "downgraded"
    assert "downgrade" not in facts.get("intents.kinds")
    # A threat filed as a subscription statement still is one.
    threat = build(subscription=[memory("mem_threat", "The customer will cancel the Pro plan unless the sync is fixed.")])
    assert "cancellation" in threat.get("intents.kinds")



def test_freshness_and_drift_are_facts_rules_can_read():
    from memory_engine.freshness import assess

    preference = memory("mem_pref", "The customer prefers email.", days_ago=60)
    problem = memory("mem_prob", "The export times out.", days_ago=50)
    problem.type, problem.status, problem.confidence = "problem", "active", 0.9
    preference.type, preference.status, preference.confidence = "preference", "active", 0.9
    flag = SimpleNamespace(
        id="drf_1", kind="channel", memory_id="mem_pref", observed="WhatsApp", counts={"observed": 6, "total": 7}
    )
    fresh = {
        "mem_pref": assess(preference, now=NOW, window_days=180, drift=[{"id": "drf_1", "kind": "channel", "summary": "x"}]),
        "mem_prob": assess(problem, now=NOW, window_days=30),
    }
    facts = build_facts(
        FactInputs(
            customer=customer(),
            now=NOW,
            memories_by_type={"preference": [preference], "problem": [problem]},
            freshness=fresh,
            drift=[flag],
        )
    )
    assert facts.get("preferences.channel") == "email", "never silently changed"
    assert facts.get("preferences.channel_outdated") is True
    assert facts.get("preferences.observed_channel") == "whatsapp"
    assert facts.get("preferences.observed_share") == 0.857
    assert facts.get("drift.open_count") == 1 and facts.get("drift.kinds") == ["channel"]
    assert facts.get("memories.stale_count") == 1 and facts.get("memories.attention_count") == 2
    assert facts.get("memories.stale_share") == 0.5
    # A reader who may not see the preference sees neither it nor that it is outdated.
    hidden = facts.redacted({"mem_pref"})
    assert hidden.get("preferences.channel") is None and hidden.get("preferences.channel_outdated") is None
    assert hidden.get("drift.kinds") == [] and hidden.get("drift.open_count") == 1, "counts stay whole"
