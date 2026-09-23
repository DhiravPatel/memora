"""Memory freshness, decay and expiry."""

from __future__ import annotations

from datetime import timedelta

from common.enums import MemoryType
from common.time import utcnow
from memory_engine.temporal.decay import expiry_for, is_stale, recency_score


def test_durable_types_never_expire():
    now = utcnow()
    assert expiry_for(MemoryType.FACT, now, 90) is None
    assert expiry_for(MemoryType.PREFERENCE, now, 90) is None
    assert expiry_for(MemoryType.SUBSCRIPTION, now, 90) is None


def test_transient_types_expire_on_a_type_specific_schedule():
    now = utcnow()
    intent = expiry_for(MemoryType.INTENT, now, 90)
    problem = expiry_for(MemoryType.PROBLEM, now, 90)
    assert intent is not None and problem is not None
    # Intent decays faster than an open problem.
    assert intent < problem


def test_decay_disabled_when_window_is_zero():
    assert expiry_for(MemoryType.INTENT, utcnow(), 0) is None


def test_recency_score_decays_with_age():
    now = utcnow()
    assert recency_score(now, MemoryType.PROBLEM) > 0.99
    three_weeks = recency_score(now - timedelta(days=21), MemoryType.PROBLEM)
    assert 0.45 < three_weeks < 0.55  # one half-life for a problem
    assert recency_score(now - timedelta(days=21), MemoryType.FACT) > three_weeks


def test_is_stale_uses_type_half_life():
    old = utcnow() - timedelta(days=60)
    assert is_stale(old, MemoryType.INTENT) is True
    assert is_stale(old, MemoryType.FACT) is False
