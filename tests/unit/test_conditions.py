"""The condition language: what it accepts, what it refuses, and how it decides.

Five features evaluate these conditions — guardrails, the lifecycle state machine,
workflows, feature flags and cohorts — so a bug here is five bugs. The tests are grouped by
the promises the module makes: parse faithfully, refuse nonsense at write time with a
useful message, evaluate with three-valued logic, and always say why.
"""

from __future__ import annotations

import pytest

from memory_engine.conditions import (
    MAX_DEPTH,
    MAX_LEAVES,
    ConditionError,
    compile_condition,
    fact_catalog,
    parse,
)
from memory_engine.facts import CustomerFacts


def facts(**values) -> CustomerFacts:
    flattened = {key.replace("__", "."): value for key, value in values.items()}
    return CustomerFacts(values=flattened)


def check(text: str, **values) -> str:
    return compile_condition(text).evaluate(facts(**values)).status


# ---------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("text", "tree"),
    [
        ("health.score < 60", {"fact": "health.score", "op": "lt", "value": 60}),
        ("health.score <= 60.5", {"fact": "health.score", "op": "lte", "value": 60.5}),
        ('health.band == "at_risk"', {"fact": "health.band", "op": "eq", "value": "at_risk"}),
        ("health.band = at_risk", {"fact": "health.band", "op": "eq", "value": "at_risk"}),
        ("health.band is at_risk", {"fact": "health.band", "op": "eq", "value": "at_risk"}),
        ("health.band is not critical", {"fact": "health.band", "op": "ne", "value": "critical"}),
        ("subscription.plan is set", {"fact": "subscription.plan", "op": "exists"}),
        ("subscription.plan is not set", {"fact": "subscription.plan", "op": "not_exists"}),
        ("subscription.plan exists", {"fact": "subscription.plan", "op": "exists"}),
        ("subscription.plan not exists", {"fact": "subscription.plan", "op": "not_exists"}),
        ('subscription.plan in ["pro", "enterprise"]', {"fact": "subscription.plan", "op": "in", "value": ["pro", "enterprise"]}),
        ("subscription.plan not in [pro]", {"fact": "subscription.plan", "op": "not_in", "value": ["pro"]}),
        ('problems.entities contains "shopify"', {"fact": "problems.entities", "op": "contains", "value": "shopify"}),
        ('problems.entities does not contain "shopify"', {"fact": "problems.entities", "op": "not_contains", "value": "shopify"}),
        ("problems.oldest_open_days between 7 and 30", {"fact": "problems.oldest_open_days", "op": "between", "value": [7, 30]}),
        ("state.pinned == true", {"fact": "state.pinned", "op": "eq", "value": True}),
    ],
)
def test_the_text_form_parses_to_the_canonical_tree(text, tree):
    assert parse(text) == tree


def test_and_binds_tighter_than_or():
    tree = parse("health.score < 60 or health.band == watch and signals.churn_risk > 0.5")
    assert tree == {
        "any": [
            {"fact": "health.score", "op": "lt", "value": 60},
            {
                "all": [
                    {"fact": "health.band", "op": "eq", "value": "watch"},
                    {"fact": "signals.churn_risk", "op": "gt", "value": 0.5},
                ]
            },
        ]
    }


def test_brackets_override_precedence():
    tree = parse("(health.score < 60 or health.band == watch) and signals.churn_risk > 0.5")
    assert list(tree) == ["all"]
    assert list(tree["all"][0]) == ["any"]


def test_symbols_are_accepted_for_the_logical_words():
    assert parse("health.score < 60 && !(health.band == critical) || state.pinned == true") == parse(
        "health.score < 60 and not (health.band == critical) or state.pinned == true"
    )


def test_between_does_not_swallow_the_next_and():
    tree = parse("problems.oldest_open_days between 7 and 30 and health.score < 60")
    assert tree["all"][0]["value"] == [7, 30]
    assert tree["all"][1]["fact"] == "health.score"


def test_the_text_round_trips():
    """Stored as JSON, shown as text, parsed back to the same tree."""
    for text in (
        'health.score < 60 and (problems.entities contains "shopify" or intents.kinds contains "cancellation")',
        'not (subscription.plan in ["enterprise", "business"])',
        "problems.oldest_open_days between 7 and 30",
        "customer.metadata.segment is set",
    ):
        condition = compile_condition(text)
        assert compile_condition(condition.text).ast == condition.ast
        assert compile_condition(condition.ast).text == condition.text


def test_json_and_text_are_interchangeable():
    from_text = compile_condition("health.score < 60")
    from_json = compile_condition({"fact": "health.score", "op": "<", "value": 60})
    from_json_string = compile_condition('{"fact": "health.score", "op": "lt", "value": 60}')
    assert from_text.ast == from_json.ast == from_json_string.ast


def test_nested_groups_of_the_same_kind_are_flattened():
    condition = compile_condition(
        {"all": [{"all": [{"fact": "health.score", "op": "lt", "value": 60}]}, {"fact": "state.pinned", "op": "exists"}]}
    )
    assert len(condition.ast["all"]) == 2


# ------------------------------------------------------- refused at write time


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("health.scor < 60", "Did you mean 'health.score'"),
        ("nonsense.fact == 1", "Unknown fact"),
        ('health.score contains "x"', "does not apply to health.score"),
        ("health.score < high", "is a number"),
        ('health.band == "at-risk"', "Did you mean 'at_risk'"),
        ('intents.kinds contains "cancel"', "Did you mean 'cancellation'"),
        ("health.score between 60 and 10", "lower bound comes first"),
        ("state.pinned == yes", "true or false"),
        ("health.score <", "Expected a value"),
        ("(health.score < 1", "Missing ')'"),
        ("health.score 60", "Expected an operator"),
        ("health.score < 60 health.band == critical", "expected 'and', 'or'"),
        ("", "empty"),
        ('problems.entities contains ""', "non-empty value"),
        ("subscription.plan in []", "non-empty list"),
    ],
)
def test_nonsense_is_refused_with_a_useful_message(text, message):
    with pytest.raises(ConditionError) as error:
        compile_condition(text)
    assert message in str(error.value)


def test_a_parse_error_reports_where_it_happened():
    with pytest.raises(ConditionError) as error:
        compile_condition("health.score < 60 and @")
    assert error.value.position == len("health.score < 60 and ")


def test_there_is_a_ceiling_on_size_and_depth():
    too_many = " or ".join(f"health.score == {index}" for index in range(MAX_LEAVES + 1))
    with pytest.raises(ConditionError, match="at most"):
        compile_condition(too_many)

    too_deep = "health.score < 1"
    for _ in range(MAX_DEPTH + 1):
        too_deep = f"not ({too_deep})"
    with pytest.raises(ConditionError, match="levels deep"):
        compile_condition(too_deep)


def test_customer_metadata_is_open_ended():
    """Your own fields do not need registering, and compare by what they hold."""
    condition = compile_condition('customer.metadata.segment == "smb" and customer.metadata.arr > 10000')
    assert condition.facts == ["customer.metadata.segment", "customer.metadata.arr"]


# -------------------------------------------------------------------- evaluation


def test_numbers_compare():
    assert check("health.score < 60", health__score=41.0) == "true"
    assert check("health.score < 60", health__score=75) == "false"
    assert check("health.score between 40 and 50", health__score=41) == "true"


def test_strings_compare_without_case():
    assert check('subscription.plan == "PRO"', subscription__plan="pro") == "true"
    assert check('subscription.plan in ["starter", "pro"]', subscription__plan="Pro") == "true"


def test_list_contains_is_membership():
    assert check('problems.entities contains "Shopify"', problems__entities=["shopify", "stripe"]) == "true"
    assert check('problems.entities contains "shop"', problems__entities=["shopify"]) == "false"


@pytest.mark.parametrize(
    ("phrase", "words", "expected"),
    [
        ("sync failures", ["sync", "keeps", "failing"], "true"),
        ("billing", ["bill", "wrong"], "true"),
        ("integration", ["integrate", "slack"], "true"),
        ("salaries", ["salary"], "true"),
        ("billing refund", ["bill", "wrong"], "false"),
    ],
)
def test_terms_match_any_form_of_every_word(phrase, words, expected):
    """A rule saying *mentions sync failures* means failing, failed and failure too."""
    assert check(f'problems.terms contains "{phrase}"', problems__terms=words) == expected


def test_metadata_numbers_sent_as_strings_still_compare():
    assert check("customer.metadata.arr > 10000", customer__metadata={"arr": "12000"}) == "true"


def test_nested_metadata_is_reachable():
    assert check('customer.metadata.billing.country == "IN"', customer__metadata={"billing": {"country": "in"}}) == "true"


def test_a_missing_value_is_unknown_not_false():
    assert check('subscription.plan == "enterprise"', subscription__plan=None) == "unknown"


def test_not_does_not_turn_unknown_into_true():
    """The whole reason for three-valued logic.

    "We do not know the plan" must not become "definitely not enterprise" — an upsell
    guard written as `not (plan == "enterprise")` would otherwise fire on every customer
    whose plan was never recorded.
    """
    evaluation = compile_condition('not (subscription.plan == "enterprise")').evaluate(
        facts(subscription__plan=None)
    )
    assert evaluation.status == "unknown"
    assert evaluation.matched is False


def test_kleene_rules_for_and_and_or():
    known_false = {"health__score": 90}
    assert check("health.score < 60 and subscription.plan == pro", subscription__plan=None, **known_false) == "false"
    assert check("health.score < 60 or subscription.plan == pro", subscription__plan=None, **known_false) == "unknown"
    assert check("health.score > 60 or subscription.plan == pro", subscription__plan=None, **known_false) == "true"


def test_exists_is_about_having_a_value():
    assert check("subscription.plan is set", subscription__plan="pro") == "true"
    assert check("subscription.plan is set", subscription__plan=None) == "false"
    assert check("problems.entities is set", problems__entities=[]) == "false"
    assert check("problems.entities is not set", problems__entities=[]) == "true"


def test_a_non_number_is_unknown_for_a_numeric_comparison():
    evaluation = compile_condition("customer.metadata.arr > 10").evaluate(
        facts(customer__metadata={"arr": "lots"})
    )
    assert evaluation.status == "unknown"
    assert "not a number" in evaluation.leaves[0].note


# ---------------------------------------------------------------- explanations


def test_the_decisive_leaves_are_the_ones_that_decided_it():
    evaluation = compile_condition("health.score < 60 or health.band == critical").evaluate(
        facts(health__score=41, health__band="at_risk")
    )
    assert evaluation.matched
    assert [leaf.fact for leaf in evaluation.decisive] == ["health.score"]


def test_a_false_and_blames_only_what_failed():
    evaluation = compile_condition("health.score < 60 and health.band == critical").evaluate(
        facts(health__score=41, health__band="at_risk")
    )
    assert evaluation.status == "false"
    assert [leaf.fact for leaf in evaluation.decisive] == ["health.band"]
    assert "health.band" in evaluation.explain()


def test_evidence_through_a_not_cites_what_made_it_true():
    """`not (open_count == 0)` holds *because* of the open problems — cite them."""
    customer = CustomerFacts(
        values={"problems.open_count": 2},
        evidence={"problems.open_count": ["mem_1", "mem_2"]},
    )
    evaluation = compile_condition("not (problems.open_count == 0)").evaluate(customer)
    assert evaluation.matched
    assert evaluation.evidence == ["mem_1", "mem_2"]


def test_contains_cites_exactly_the_memories_that_mention_it():
    customer = CustomerFacts(
        values={"problems.entities": ["shopify", "stripe"]},
        evidence={"problems.entities": ["mem_1", "mem_2", "mem_3"]},
        evidence_by_value={"problems.entities": {"shopify": ["mem_2"], "stripe": ["mem_1"]}},
    )
    evaluation = compile_condition('problems.entities contains "shopify"').evaluate(customer)
    assert evaluation.evidence == ["mem_2"]


def test_every_leaf_is_traced_even_when_not_decisive():
    evaluation = compile_condition("health.score < 60 or health.band == critical").evaluate(
        facts(health__score=41, health__band="at_risk")
    )
    assert [leaf.fact for leaf in evaluation.leaves] == ["health.score", "health.band"]
    assert evaluation.as_dict()["leaves"][1]["outcome"] == "false"


# ------------------------------------------------------------------------ catalog


def test_the_catalog_lists_every_fact_with_its_operators():
    catalog = {entry["name"]: entry for entry in fact_catalog()}
    assert "health.score" in catalog
    assert "<" in catalog["health.score"]["operators"]
    assert "contains" not in catalog["health.score"]["operators"]
    assert "contains" in catalog["problems.terms"]["operators"]
    assert catalog["health.band"]["values"] == ["healthy", "watch", "at_risk", "critical"]
