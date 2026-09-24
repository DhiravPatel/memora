"""The concept lexicon: paraphrases that share no words must share concepts."""

from __future__ import annotations

import pytest

from nlp.concepts import CONCEPTS, concept_hits, concepts_for, dice


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("The integration is completely broken.", "The connector has stopped functioning."),
        ("The sync is hosed.", "The integration keeps failing."),
        ("Checkout keeps timing out.", "The cart is really slow."),
        ("They were double charged.", "The invoice is wrong and they want a refund."),
        ("The loader dies every night.", "The importer crashed again."),
        ("They cannot sign in since SSO changed.", "Login is broken after the SAML update."),
    ],
)
def test_paraphrases_share_their_concepts(one: str, other: str):
    """The case §25 called unsolved: no shared content word, same complaint."""
    assert set(concepts_for(one)) & set(concepts_for(other))


def test_the_matching_phrase_is_reported():
    hits = {hit.concept: hit.phrase for hit in concept_hits("The connector has stopped functioning.")}
    assert hits == {"integration": "connector", "broken": "stopped functioning"}


def test_inflection_and_derivation_reach_the_same_concept():
    for text in ("It fails.", "It failed.", "It is failing.", "A failure happened."):
        assert "broken" in concepts_for(text), text


def test_the_longest_phrase_wins():
    """'data loader' is one phrase, not 'data' then 'loader'."""
    assert [hit.phrase for hit in concept_hits("The data loader is fine")] == ["data loader"]


def test_contractions_are_expanded_before_matching():
    assert "broken" in concepts_for("It doesn't work.")


def test_unrelated_text_has_no_concepts():
    assert concepts_for("The weather in Lisbon was lovely.") == []


def test_dice_rewards_being_about_exactly_the_question():
    assert dice({"broken", "integration"}, {"broken", "integration"}) == 1.0
    assert dice({"broken", "integration"}, {"broken", "integration", "orders"}) == pytest.approx(0.8)
    assert dice({"broken"}, {"billing"}) == 0.0
    assert dice(set(), {"broken"}) == 0.0


def test_every_concept_has_a_label_and_phrases():
    for concept, (label, phrases) in CONCEPTS.items():
        assert label and phrases, concept
