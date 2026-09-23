"""Statement classification: the decision that gives every memory its type."""

from __future__ import annotations

import pytest

from common.enums import MemoryType
from nlp.classify import classify


@pytest.mark.parametrize(
    ("text", "event_type", "expected"),
    [
        ("I've tried connecting Shopify three times but it still doesn't work", "support_message", MemoryType.PROBLEM),
        ("Please contact me on WhatsApp instead of email", "support_message", MemoryType.PREFERENCE),
        ("We want to migrate 2M records before August", "", MemoryType.GOAL),
        ("We are considering switching to a competitor", "", MemoryType.INTENT),
        ("The dashboard is great, we love the new charts", "feedback", MemoryType.FEEDBACK),
        ("Our invoice was charged twice this month", "support_message", MemoryType.PROBLEM),
        ("used the campaign builder", "feature_used", MemoryType.BEHAVIOR),
    ],
)
def test_classification(text, event_type, expected):
    result = classify(text, event_type=event_type)
    assert result.type is expected, result.explain()
    assert 0.0 < result.confidence <= 0.99


def test_negated_problem_is_not_a_problem():
    """"No problem at all" must not become a problem memory."""
    result = classify("No problem at all, just checking in", event_type="support_message")
    assert result.type is not MemoryType.PROBLEM
    assert "problem" in result.negated_cues


def test_resolution_is_detected():
    result = classify("It works now, thanks for fixing it", event_type="support_message")
    assert result.resolved is True
    assert result.type is not MemoryType.PROBLEM


def test_event_type_prior_applies_when_text_is_ambiguous():
    """With no cues in the sentence, the event type decides — and nothing else does."""
    neutral = "Following up on the thread from yesterday"
    assert classify(neutral, event_type="support_message").type is MemoryType.PROBLEM
    assert classify(neutral, event_type="feature_used").type is MemoryType.BEHAVIOR
    assert classify(neutral, event_type="").type is MemoryType.FACT


def test_cues_outrank_the_event_type_prior():
    """A sentence that clearly states a preference is not a problem, whatever carried it."""
    result = classify("Please contact me on WhatsApp instead", event_type="support_message")
    assert result.type is MemoryType.PREFERENCE


def test_explanation_names_the_cue_that_fired():
    result = classify("The integration keeps failing", event_type="")
    explanation = result.explain()
    assert explanation["type"] == "problem"
    assert any("fail" in cue for cue in explanation["cues"])
    assert explanation["scores"]


def test_cues_match_on_word_boundaries():
    """"migrate" must not fire the "rating" cue."""
    result = classify("We want to migrate 2M records", event_type="")
    assert "rating" not in result.cues
