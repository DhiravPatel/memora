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


def test_a_strong_event_type_prior_decides_ambiguous_text():
    """With no cues in the sentence, a strong event type decides: a feature_used event is
    behaviour whatever it says."""
    neutral = "Following up on the thread from yesterday"
    assert classify(neutral, event_type="feature_used").type is MemoryType.BEHAVIOR
    assert classify(neutral, event_type="").type is MemoryType.FACT


def test_a_weak_prior_only_reinforces_evidence_for_its_own_type():
    """Where a message was sent is not what it says. A support thread carries news and
    thanks as well as complaints; reading every neutral sentence in one as a problem wrote
    open problems nobody had."""
    assert classify("Following up on the thread from yesterday", event_type="support_message").type is MemoryType.FACT
    news = classify("Weekly campaigns to all 50k subscribers are now live, it went out this morning.", event_type="support_message")
    assert news.type is not MemoryType.PROBLEM
    # With evidence of a problem, the prior still adds its weight.
    slow = classify("The export is slow and keeps timing out.", event_type="support_message")
    assert slow.type is MemoryType.PROBLEM and "timing out" in slow.cues


def test_negation_stays_inside_its_clause():
    """"will not connect, it keeps rejecting" is two complaints — the "not" of the first
    must not deny the second."""
    result = classify("QuickBooks will not connect, it keeps rejecting the customer's credentials.", event_type="support_message")
    assert result.type is MemoryType.PROBLEM
    assert "rejected" in result.cues and "rejected" not in result.negated_cues
    assert "connect" in result.negated_cues  # negated where it stands: "will not connect"
    # Inside one clause a negation still denies: "no problem" is not a problem report.
    assert classify("No problem at all, thanks", event_type="").type is not MemoryType.PROBLEM


@pytest.mark.parametrize(
    "sentence",
    [
        "We won't be able to log in until SSO is fixed.",
        "Once the sync is fixed we will roll out to every store.",
        "Please get this sorted before Friday.",
        "It needs to be fixed today.",
    ],
)
def test_a_fix_still_to_come_is_not_a_resolution(sentence):
    assert classify(sentence, event_type="support_message").resolved is False


@pytest.mark.parametrize(
    "sentence",
    ["It works now, thanks for fixing it so quickly.", "The Shopify sync works now.", "The sync issue is resolved."],
)
def test_a_fix_that_happened_is(sentence):
    assert classify(sentence, event_type="support_message").resolved is True


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
