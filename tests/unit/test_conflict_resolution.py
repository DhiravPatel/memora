"""Conflicting statements: newer and more explicit usually wins, but never silently."""

from __future__ import annotations

from datetime import timedelta

from common.time import utcnow
from memory_engine.consolidation.conflict import (
    ConflictSide,
    explicitness,
    frequency_score,
    resolve,
)


def test_recent_explicit_request_overrides_old_inference():
    outcome = resolve(
        existing=ConflictSide(
            content="Customer prefers email.",
            confidence=0.7,
            last_seen_at=utcnow() - timedelta(days=120),
            evidence_count=1,
        ),
        candidate=ConflictSide(
            content="Please contact me on WhatsApp from now on.",
            confidence=0.95,
            last_seen_at=utcnow(),
            evidence_count=1,
        ),
    )
    assert outcome.candidate_wins
    assert outcome.candidate_score > outcome.existing_score


def test_well_supported_memory_survives_a_weak_contradiction():
    outcome = resolve(
        existing=ConflictSide(
            content="Customer prefers email.",
            confidence=0.95,
            last_seen_at=utcnow() - timedelta(days=2),
            evidence_count=8,
            source="manual",
        ),
        candidate=ConflictSide(
            content="Maybe they use Slack sometimes.",
            confidence=0.3,
            last_seen_at=utcnow(),
            evidence_count=1,
        ),
    )
    assert not outcome.candidate_wins


def test_ties_keep_the_incumbent():
    side = {"content": "Same statement.", "confidence": 0.8, "evidence_count": 2}
    now = utcnow()
    outcome = resolve(
        existing=ConflictSide(last_seen_at=now, **side),
        candidate=ConflictSide(last_seen_at=now, **side),
    )
    assert outcome.winner == "existing"


def test_explicit_language_scores_higher():
    assert explicitness("Please stop emailing me, contact me on WhatsApp instead.") > explicitness(
        "Opened the billing page."
    )


def test_frequency_has_diminishing_returns():
    first_jump = frequency_score(2) - frequency_score(1)
    later_jump = frequency_score(9) - frequency_score(8)
    assert first_jump > later_jump
