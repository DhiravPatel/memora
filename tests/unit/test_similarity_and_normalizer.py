"""Similarity blending, deduplication and event normalization."""

from __future__ import annotations

from datetime import UTC, datetime

from memory_engine.consolidation.similarity import (
    combined_similarity,
    cosine_similarity,
    is_exact_duplicate,
    text_similarity,
)
from memory_engine.extraction.normalizer import flatten_payload, normalize_event


def test_cosine_similarity_bounds():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([], [1]) == 0.0
    assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0


def test_exact_duplicate_ignores_case_and_whitespace():
    assert is_exact_duplicate("Customer uses Shopify.", "  customer uses shopify.  ")
    assert not is_exact_duplicate("Customer uses Shopify.", "Customer uses Stripe.")


def test_combined_similarity_penalises_vector_only_agreement():
    """Two different problems can look close in vector space; lexical overlap tempers that."""
    unrelated = combined_similarity(0.95, "Customer cannot connect Shopify.", "Customer loves the mobile app.")
    related = combined_similarity(0.95, "Customer cannot connect Shopify.", "Customer's Shopify connection fails.")
    assert related > unrelated
    assert unrelated < 0.95


def test_text_similarity_ignores_stopwords():
    assert text_similarity("the customer is in the app", "a customer is on an app") > 0.5


def test_flatten_payload_renders_nested_values():
    lines = flatten_payload({"message": "hi", "meta": {"plan": "pro"}, "tags": ["a", "b"]})
    assert "message: hi" in lines
    assert "meta.plan: pro" in lines
    assert "tags: a, b" in lines


def test_normalize_event_redacts_pii_and_scores_importance():
    event = normalize_event(
        event_id="evt_1",
        project_id="prj_1",
        customer_id="cus_1",
        event_type="Support_Message",
        data={"message": "Email me at john@example.com", "api_key": "sk_live_abcdef123456"},
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert event.event_type == "support_message"
    assert "john@example.com" not in event.text
    assert "[REDACTED_EMAIL]" in event.text
    assert event.data["api_key"] == "[REDACTED]"
    assert event.importance >= 0.6


def test_normalize_event_can_keep_raw_text_when_redaction_is_disabled():
    event = normalize_event(
        event_id="evt_1",
        project_id="prj_1",
        customer_id="cus_1",
        event_type="support_message",
        data={"message": "Email me at john@example.com"},
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        redact_pii=False,
    )
    assert "john@example.com" in event.text
