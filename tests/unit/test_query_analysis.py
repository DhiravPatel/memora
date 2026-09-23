"""Query understanding: time windows, memory types and entity names."""

from __future__ import annotations

from common.enums import MemoryType
from common.time import days_between, utcnow
from memory_engine.retrieval.query_analysis import analyze
from nlp.tokenize import lemmatize


def test_detects_problem_queries():
    analysis = analyze("What problems has this customer experienced?")
    assert MemoryType.PROBLEM in analysis.types


def test_detects_billing_queries():
    analysis = analyze("Has their subscription plan changed?")
    assert MemoryType.SUBSCRIPTION in analysis.types


def test_relative_time_windows():
    recent = analyze("What happened recently?")
    assert recent.wants_recent and recent.since is not None
    assert 29 <= days_between(recent.since, utcnow()) <= 31

    explicit = analyze("Anything in the last 2 weeks?")
    assert explicit.since is not None
    assert 13 <= days_between(explicit.since, utcnow()) <= 15


def test_no_time_window_when_not_asked():
    assert analyze("What does this customer use?").since is None


def test_extracts_known_integration_names():
    analysis = analyze("Is Shopify still failing for them?")
    assert "Shopify" in analysis.entity_names


def test_keywords_are_lemmatised_and_stopword_free():
    """Keywords are lemmas because they are matched against lemmatised memory text."""
    analysis = analyze("Why did the customer downgrade their subscription?")
    assert lemmatize("downgrade") in analysis.keywords
    assert "the" not in analysis.keywords
    assert "their" not in analysis.keywords
