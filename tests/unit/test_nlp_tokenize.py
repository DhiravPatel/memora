"""Tokenisation, sentence splitting and lemmatisation.

These are the foundation of every downstream decision: if "downgraded" and "downgrade" do
not reduce to the same stem, causal linking silently stops working.
"""

from __future__ import annotations

import pytest

from nlp.tokenize import (
    content_words,
    correct_spelling,
    expand_contractions,
    is_question,
    lemmatize,
    split_clauses,
    split_sentences,
    tokenize,
)


def test_sentences_split_on_punctuation_and_newlines():
    text = "Hi. I've tried connecting Shopify 3 times but it still doesn't work!\n- Billing is fine"
    sentences = split_sentences(text)
    assert sentences[0] == "Hi."
    assert "Shopify" in sentences[1]
    assert sentences[-1] == "Billing is fine"


def test_abbreviations_do_not_end_a_sentence():
    assert len(split_sentences("Contact Dr. Smith about the invoice.")) == 1


def test_clauses_split_on_contrast():
    assert split_clauses("Shopify works but billing fails") == ["Shopify works", "billing fails"]
    assert split_clauses("Everything is fine") == ["Everything is fine"]


@pytest.mark.parametrize(
    "group",
    [
        ("upgrade", "upgraded", "upgrades", "upgrading"),
        ("use", "used", "using", "uses"),
        ("cancel", "cancelled", "cancellation", "cancelling"),
        ("fail", "failed", "failing"),
        ("connect", "connected", "connecting"),
        ("charge", "charged", "charges"),
        ("time", "times"),
    ],
)
def test_inflections_share_a_stem(group):
    stems = {lemmatize(word) for word in group}
    assert len(stems) == 1, f"{group} produced {stems}"


def test_distinct_words_keep_distinct_stems():
    assert lemmatize("billing") != lemmatize("bill")
    assert lemmatize("integration") != lemmatize("integrity")


def test_contractions_expand_case_preserving():
    assert expand_contractions("It doesn't work") == "It does not work"
    assert "Shopify" in expand_contractions("Shopify can't connect")


def test_tokenize_folds_possessives():
    assert "customer" in tokenize("the customer's account")
    assert "s" not in tokenize("the customer's account")


def test_content_words_drop_stopwords():
    words = content_words("I have tried connecting the Shopify integration")
    assert "shopify" in words
    assert "the" not in words and "have" not in words


def test_spelling_correction_reaches_the_gazetteer():
    assert "shopify" in correct_spelling("shopfy is down").lower()


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Why did they leave?", True), ("Can you check", True), ("The sync failed", False)],
)
def test_question_detection(text, expected):
    assert is_question(text) is expected
