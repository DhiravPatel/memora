"""The consolidation decision, branch by branch."""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.enums import ConsolidationAction, MemoryType
from common.time import utcnow
from memory_engine.consolidation.rules import (
    CandidateSnapshot,
    MemorySnapshot,
    current_plan,
    decide,
    detect_contradiction,
    is_transition,
    merge_content,
    novelty,
    recurrence_note,
)

NOW = utcnow()


def existing(
    content: str,
    *,
    memory_type: MemoryType = MemoryType.PROBLEM,
    evidence: int = 1,
    first_days: int = 10,
    entities: tuple[str, ...] = (),
) -> MemorySnapshot:
    return MemorySnapshot(
        id="mem_existing",
        type=memory_type,
        content=content,
        confidence=0.9,
        importance=0.8,
        evidence_count=evidence,
        first_seen_at=NOW - timedelta(days=first_days),
        last_seen_at=NOW - timedelta(days=2),
        entities=entities,
    )


def candidate(
    content: str,
    *,
    memory_type: MemoryType = MemoryType.PROBLEM,
    entities: tuple[str, ...] = (),
    attributes: dict | None = None,
) -> CandidateSnapshot:
    return CandidateSnapshot(
        type=memory_type,
        content=content,
        confidence=0.92,
        importance=0.8,
        occurred_at=NOW,
        entities=entities,
        attributes=attributes or {},
    )


def test_identical_content_merges_without_comparison():
    decision = decide(
        existing=existing("The Shopify integration keeps failing."),
        candidate=candidate("The Shopify integration keeps failing."),
        similarity=0.99,
    )
    assert decision.action is ConsolidationAction.MERGE
    assert decision.signals["rule"] == "exact_duplicate"


def test_unrelated_statements_create_a_new_memory():
    decision = decide(
        existing=existing("The Shopify integration keeps failing."),
        candidate=candidate("The customer uses the Campaign Builder feature.",
                            memory_type=MemoryType.BEHAVIOR),
        similarity=0.2,
    )
    assert decision.action is ConsolidationAction.CREATE


def test_different_memory_types_never_merge():
    """A problem must not be absorbed into a fact just because the words overlap."""
    decision = decide(
        existing=existing("The customer uses the Shopify integration.", memory_type=MemoryType.FACT),
        candidate=candidate("The customer's Shopify integration failed.", entities=("Shopify",)),
        similarity=0.9,
    )
    assert decision.action is ConsolidationAction.CREATE
    assert decision.signals["rule"] == "type_mismatch"


def test_new_detail_updates_the_memory():
    decision = decide(
        existing=existing("The Shopify integration keeps failing.", entities=("Shopify",)),
        candidate=candidate(
            "The Shopify integration failed with an OAuth handshake timeout during checkout.",
            entities=("Shopify",),
        ),
        similarity=0.6,
    )
    assert decision.action is ConsolidationAction.UPDATE
    assert "OAuth" in decision.content


def test_repetition_adds_a_factual_recurrence_note():
    decision = decide(
        existing=existing("The Shopify integration keeps failing.", evidence=2, entities=("Shopify",)),
        candidate=candidate("Shopify sync failed again.", entities=("Shopify",)),
        similarity=0.7,
    )
    assert "Reported 3 times since" in decision.content


def test_changed_contact_channel_is_a_conflict():
    decision = decide(
        existing=existing("The customer prefers email.", memory_type=MemoryType.PREFERENCE),
        candidate=candidate("Please contact the customer on WhatsApp.",
                            memory_type=MemoryType.PREFERENCE),
        similarity=0.5,
    )
    assert decision.action is ConsolidationAction.CONFLICT
    assert "channel" in decision.reason


def test_resolution_is_a_conflict_with_the_open_problem():
    decision = decide(
        existing=existing("The Shopify integration keeps failing.", entities=("Shopify",)),
        candidate=candidate("The Shopify integration is working again.",
                            entities=("Shopify",), attributes={"resolved": True}),
        similarity=0.7,
    )
    assert decision.action is ConsolidationAction.CONFLICT
    assert "resolved" in decision.reason


def test_subscription_transitions_are_history_not_contradictions():
    decision = decide(
        existing=existing("The customer upgraded from the Starter plan to the Pro plan.",
                          memory_type=MemoryType.SUBSCRIPTION),
        candidate=candidate("The customer downgraded from the Pro plan to the Starter plan.",
                            memory_type=MemoryType.SUBSCRIPTION),
        similarity=0.6,
    )
    assert decision.action is ConsolidationAction.CREATE
    assert decision.signals["rule"] == "distinct_transition"


def test_state_statements_about_different_plans_do_conflict():
    contradicts, reason = detect_contradiction(
        existing("The customer is on the Pro plan.", memory_type=MemoryType.SUBSCRIPTION),
        candidate("The customer is on the Starter plan.", memory_type=MemoryType.SUBSCRIPTION),
    )
    assert contradicts and "current plan" in reason


def test_merge_content_keeps_one_clean_statement():
    """Content is the current best statement; history lives in memory_versions."""
    merged = merge_content(
        existing("The Shopify integration keeps failing."),
        candidate("The Shopify integration fails during checkout with a timeout."),
        None,
    )
    assert "Also:" not in merged
    assert "checkout" in merged


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The customer upgraded from the Starter plan to the Pro plan.", True),
        ("The customer is on the Pro plan.", False),
    ],
)
def test_transition_detection(text, expected):
    assert is_transition(text) is expected


def test_current_plan_reads_the_destination():
    assert current_plan("The customer downgraded from the Pro plan to the Starter plan.") == "Starter"
    assert current_plan("The customer is on the Pro plan.") == "Pro"


def test_novelty_and_recurrence_helpers():
    ratio, words = novelty("Shopify keeps failing", "Shopify fails during checkout")
    assert 0 < ratio <= 1 and "checkout" in words
    assert recurrence_note(existing("x", evidence=1), candidate("y")) is None
    assert recurrence_note(existing("x", evidence=5), candidate("y")).startswith("Reported 6 times")
