"""Mining a project's own vocabulary, and knowing when a summary has gone stale.

The mining rules exist to stop the system teaching itself nonsense: a pair has to be
supported by several different memories, has to beat chance by a wide margin, and cannot
come from a word that appears in everything.
"""

from __future__ import annotations

import pytest

from nlp.mining import (
    MAX_PAIRS_PER_TERM,
    MIN_SUPPORT,
    LearnedPair,
    as_expansion_table,
    mine,
    terms_of,
)
from nlp.question import analyze, expand
from worker.tasks.summarize import MIN_MEMORIES, drift_threshold, has_drifted

SYNC_STORY = [
    "the shopify sync is failing again",
    "shopify sync failed overnight for us",
    "our shopify sync keeps failing every morning",
    "shopify sync broke once more today",
]
BILLING_STORY = [
    "the invoice pdf is broken",
    "invoice pdf download is broken",
    "the invoice pdf will not open at all",
]
NOISE = [
    "the team loves the new reporting",
    "we added three seats this month",
    "thanks for the quick reply yesterday",
]


def corpus(*groups: list[str]) -> list[list[str]]:
    return [terms_of(text) for group in groups for text in group]


def pairs_of(result: list[LearnedPair]) -> set[frozenset[str]]:
    return {frozenset((pair.term, pair.synonym)) for pair in result}


# ------------------------------------------------------------------- tokenising


def test_short_product_words_survive_tokenising():
    """"sso", "api" and "pdf" are exactly the vocabulary worth learning."""
    terms = terms_of("the sso login and the api both use pdf export")
    assert "sso" in terms
    assert "api" in terms
    assert "pdf" in terms


def test_stopwords_and_numbers_are_dropped():
    terms = terms_of("the and 12345 shopify")
    assert terms == ["shopify"]


def test_a_term_appears_once_per_memory():
    """Repetition inside one memory must not look like independent support."""
    assert terms_of("sync sync sync failing") == terms_of("sync failing")


# ---------------------------------------------------------------------- mining


def test_terms_that_travel_together_are_learned():
    learned = mine(corpus(SYNC_STORY, BILLING_STORY, NOISE))
    assert frozenset(("shopify", "sync")) in pairs_of(learned)


def test_unrelated_terms_are_not_learned():
    learned = mine(corpus(SYNC_STORY, BILLING_STORY, NOISE))
    assert frozenset(("shopify", "invoic")) not in pairs_of(learned)


def test_a_single_memory_cannot_teach_a_pair():
    """One rambling support thread is not evidence."""
    learned = mine(corpus(NOISE, ["quantum flux capacitor overdrive"]))
    assert not any("quantum" in (pair.term, pair.synonym) for pair in learned)


def test_support_is_reported_and_meets_the_floor():
    for pair in mine(corpus(SYNC_STORY, BILLING_STORY, NOISE)):
        assert pair.support >= MIN_SUPPORT
        assert 0 < pair.score <= 1


def test_a_small_corpus_produces_nothing_rather_than_noise():
    assert mine([terms_of("one lonely memory")]) == []


def test_mining_is_reproducible():
    documents = corpus(SYNC_STORY, BILLING_STORY, NOISE)
    first = [pair.as_dict() for pair in mine(documents)]
    second = [pair.as_dict() for pair in mine(documents)]
    assert first == second


def test_a_word_in_everything_is_ignored():
    """A term that co-occurs with all others discriminates nothing."""
    documents = [terms_of(f"customer account {topic}") for topic in
                 ("sync failing", "invoice broken", "report slow", "login denied",
                  "export stuck", "import failed")]
    learned = mine(documents)
    assert not any("customer" in (pair.term, pair.synonym) for pair in learned)


def test_no_single_term_can_fill_the_table():
    documents = [terms_of(f"sync {other}") for other in
                 ("alpha alpha", "beta beta", "gamma gamma", "delta delta", "epsilon epsilon")] * 3
    learned = mine(documents)
    sync_pairs = [pair for pair in learned if "sync" in (pair.term, pair.synonym)]
    assert len(sync_pairs) <= MAX_PAIRS_PER_TERM


def test_the_expansion_table_works_in_both_directions():
    table = as_expansion_table([LearnedPair(term="sync", synonym="handoff", score=0.9, support=5)])
    assert table["sync"] == ("handoff",)
    assert table["handoff"] == ("sync",)


# ----------------------------------------------------------------- retrieval use


def test_learned_terms_widen_a_question():
    """The point of the whole feature: a paraphrase with no shared vocabulary matches."""
    without = expand(["loader"])
    with_learned = expand(["loader"], learned={"loader": ("importer",)})
    assert "importer" not in without
    assert "importer" in with_learned


def test_shipped_synonyms_still_apply_when_a_project_has_learned_its_own():
    analysis = analyze("billing problem", learned_synonyms={"billing": ("ledger",)})
    # The shipped table maps billing → invoice; the learned one adds ledger.
    assert "invoice" in analysis.expanded_keywords
    assert "ledger" in analysis.expanded_keywords


def test_analysis_without_learned_terms_is_unchanged():
    assert analyze("billing problem").expanded_keywords == analyze(
        "billing problem", learned_synonyms={}
    ).expanded_keywords


# ------------------------------------------------------------------ summary drift


@pytest.mark.parametrize(
    ("covered", "current", "stale"),
    [
        (0, MIN_MEMORIES - 1, False),   # too few memories to summarise at all
        (0, MIN_MEMORIES, True),        # first summary is due
        (6, 7, False),                  # one more memory is not a new customer
        (6, 8, True),                   # a quarter of six, rounded, is two
        (200, 203, False),              # three out of two hundred is noise
        (200, 205, True),               # five is the absolute ceiling
        (20, 15, True),                 # memories disappearing counts as drift too
    ],
)
def test_summary_drift(covered: int, current: int, stale: bool):
    assert has_drifted(covered=covered, current=current) is stale


def test_the_drift_threshold_is_never_zero():
    """A threshold of zero would rewrite the summary on every single event."""
    assert all(drift_threshold(covered) >= 1 for covered in range(0, 500))
