"""First person to third person, with the verb agreeing wherever the customer is the subject."""

from __future__ import annotations

import pytest

from nlp.rewrite import third_person, to_third_person


@pytest.mark.parametrize(
    ("said", "remembered"),
    [
        ("Please don't call us, we prefer email.", "Please do not call the customer, the customer prefers email."),
        ("We also use Slack for alerts.", "The customer also uses Slack for alerts."),
        ("If we cancel, it will be because of the export.", "If the customer cancels, it will be because of the export."),
        ("When we export payroll, the file is empty.", "When the customer exports payroll, the file is empty."),
        ("We sync orders hourly and we rely on it.", "The customer syncs orders hourly and the customer relies on it."),
        ("We process payroll every Friday.", "The customer processes payroll every Friday."),
        ("I have two stores and we ship to Canada.", "The customer has two stores and the customer ships to Canada."),
        ("We only need one seat.", "The customer only needs one seat."),
        ("We do the payroll in-house.", "The customer does the payroll in-house."),
        ("we go live next week", "The customer goes live next week."),
        # Once named, the customer is referred back to.
        ("We want to migrate our whole contact list.", "The customer wants to migrate their whole contact list."),
        ("Can you send us our invoice?", "Can you send the customer their invoice?"),
        ("Please help us plan our rollout.", "Please help the customer plan their rollout."),
        ("We have two stores and our warehouse is in Leeds.", "The customer has two stores and their warehouse is in Leeds."),
    ],
)
def test_the_verb_agrees_with_the_customer_as_subject(said, remembered):
    assert to_third_person(said).text == remembered


@pytest.mark.parametrize(
    ("said", "remembered"),
    [
        # After a verb, "the customer" is an object and what follows is a bare infinitive.
        ("Please help us plan the rollout.", "Please help the customer plan the rollout."),
        ("Let us know when it is fixed.", "Let the customer know when it is fixed."),
        ("Can you send us the invoice?", "Can you send the customer the invoice?"),
        # Already agreed, past, or auxiliary: left alone.
        ("We tried to connect Shopify.", "The customer tried to connect Shopify."),
        ("We are unhappy.", "The customer is unhappy."),
        ("Our team uses the API.", "The customer's team uses the API."),
        ("I lost my password and my data.", "The customer lost their password and their data."),
        ("We sent the file and we cut the budget.", "The customer sent the file and the customer cut the budget."),
    ],
)
def test_objects_and_agreed_verbs_are_left_alone(said, remembered):
    assert to_third_person(said).text == remembered


def test_third_person_forms():
    assert [third_person(word) for word in ("want", "push", "carry", "play", "go", "have", "rely", "focus")] == [
        "wants", "pushes", "carries", "plays", "goes", "has", "relies", "focuses",
    ]
    assert [third_person(word) for word in ("tried", "using", "uses", "is", "will", "an")] == [None] * 6
