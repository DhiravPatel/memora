"""Cheap triage must keep telemetry out of the LLM and let real signal through."""

from __future__ import annotations

import pytest

from memory_engine.extraction.filters import base_importance, score_event, should_extract


def test_known_low_value_events_score_near_zero():
    assert score_event(event_type="page_view", data={}) <= 0.1
    assert score_event(event_type="heartbeat", data={}) == 0.0


def test_known_high_value_events_score_high():
    assert score_event(event_type="cancellation_requested", data={}) >= 0.95
    assert score_event(event_type="subscription_downgraded", data={}) >= 0.85


def test_unknown_event_type_uses_markers():
    assert base_importance("shopify_payment_failed") == 0.7
    assert base_importance("dashboard_view") == 0.05
    assert base_importance("something_unusual") == 0.4


def test_free_text_lifts_importance():
    """A human writing a sentence is almost always worth remembering."""
    low = score_event(event_type="page_view", data={})
    with_text = score_event(
        event_type="page_view", data={"message": "This integration has never worked for us."}
    )
    assert with_text > low
    assert with_text >= 0.6


def test_project_overrides_win():
    assert score_event(event_type="page_view", data={}, overrides={"page_view": 0.9}) == 0.9


def test_explicit_importance_in_payload_wins():
    assert score_event(event_type="page_view", data={"importance": 0.77}) == 0.77


@pytest.mark.parametrize(
    ("importance", "threshold", "expected"),
    [(0.2, 0.2, True), (0.19, 0.2, False), (1.0, 0.9, True)],
)
def test_should_extract(importance, threshold, expected):
    assert should_extract(importance, threshold) is expected
