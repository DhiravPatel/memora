"""Sentiment, and the one rule every feature reads feedback's tone by."""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.enums import MemoryType
from common.time import utcnow
from memory_engine.analytics import compute
from memory_engine.facts import is_negative
from nlp.answer import MemoryView
from nlp.lexicon import verb_forms
from nlp.sentiment import analyze, feedback_band, feedback_strength, feedback_tone


@pytest.mark.parametrize(
    ("text", "sign"),
    [
        ("The customer loves the reporting.", 1),
        ("My team loved the onboarding.", 1),
        ("The customer praised the new editor.", 1),
        ("The customer hates the new editor.", -1),
        ("The customer complained about the invoices.", -1),
        ("The export fails on large files.", -1),
        ("The app crashes every morning.", -1),
        ("We are not happy with the sync.", -1),
    ],
)
def test_inflected_sentiment_words_count(text: str, sign: int):
    polarity = analyze(text).polarity
    assert polarity * sign > 0.4, (text, polarity)


def test_the_negation_bait_is_not_inflected():
    """"load", "sync", "open" are there for "does not load"; "keeps loading" is not praise."""
    assert analyze("The page keeps loading forever.").polarity == 0.0
    assert analyze("The dashboard opens.").polarity == 0.0
    assert analyze("The dashboard does not load.").polarity < 0, "what the bait is for"


def test_verb_forms():
    assert verb_forms("love") == ("love", "loves", "loved", "loving")
    assert verb_forms("crash") == ("crash", "crashes", "crashed", "crashing")
    assert verb_forms("complain") == ("complain", "complains", "complained", "complaining")


def test_a_score_speaks_through_its_band():
    assert feedback_band("The customer gave a satisfaction score of 4 (detractor).") == "detractor"
    assert feedback_band("anything", {"band": "Promoter"}) == "promoter"
    assert feedback_band("The customer loves it.") is None
    assert feedback_tone("The customer gave a satisfaction score of 4 (detractor).") == "negative"
    assert feedback_tone("The customer gave a satisfaction score of 9 (promoter).") == "positive"
    assert feedback_tone("The customer gave a satisfaction score of 7 (passive).") == "neutral"
    assert feedback_strength("The customer gave a satisfaction score of 9 (promoter).") == 1.0
    assert feedback_strength("The customer gave a satisfaction score of 7 (passive).") == 0.0


def test_words_speak_through_their_sentiment_recorded_or_read():
    assert feedback_tone("meh", {"sentiment": {"polarity": -0.4}}) == "negative"
    assert feedback_tone("The customer loves the reporting.") == "positive", "read from the words when none was recorded"
    assert feedback_tone("The customer mentioned the reporting.") == "neutral"
    memory = type("M", (), {"content": "The customer gave a satisfaction score of 3 (detractor).", "meta": {}})()
    assert is_negative(memory), "the fact document counts a detractor"


def test_health_reads_tone_not_importance():
    """A promoter's 9 and a furious one-liner can carry the same importance."""
    now = utcnow()

    def feedback(ident: str, content: str, importance: float) -> MemoryView:
        return MemoryView(
            id=ident, type=MemoryType.FEEDBACK, content=content, importance=importance,
            last_seen_at=now - timedelta(days=1), first_seen_at=now - timedelta(days=1),
        )

    promoter = compute(memories=[feedback("f1", "The customer gave a satisfaction score of 9 (promoter).", 0.7)], event_count=5)
    assert {factor.key for factor in promoter.factors} >= {"positive_feedback"}
    assert "negative_feedback" not in {factor.key for factor in promoter.factors}
    furious = compute(memories=[feedback("f2", "The customer hates the new editor.", 0.6)], event_count=5)
    assert "negative_feedback" in {factor.key for factor in furious.factors}
    assert "positive_feedback" not in {factor.key for factor in furious.factors}
