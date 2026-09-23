"""Answer composition: the deterministic replacement for asking a model."""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.enums import MemoryType
from common.time import utcnow
from nlp.answer import MemoryView, compose
from nlp.question import analyze

NOW = utcnow()


def memory(
    identifier: str,
    memory_type: MemoryType,
    content: str,
    *,
    days: int = 2,
    importance: float = 0.8,
    evidence: int = 1,
    entities: tuple[str, ...] = (),
    attributes: dict | None = None,
) -> MemoryView:
    return MemoryView(
        id=identifier,
        type=memory_type,
        content=content,
        importance=importance,
        confidence=0.9,
        last_seen_at=NOW - timedelta(days=days),
        first_seen_at=NOW - timedelta(days=days + 4),
        evidence_count=evidence,
        source_event_ids=[f"evt_{identifier}"],
        score=0.7,
        entities=list(entities),
        attributes=attributes or {},
    )


@pytest.fixture
def story() -> list[MemoryView]:
    return [
        memory("m1", MemoryType.SUBSCRIPTION,
               "The customer downgraded from the Pro plan to the Starter plan.", days=1,
               entities=("Pro",)),
        memory("m2", MemoryType.PROBLEM, "The Shopify integration still does not work.", days=3,
               evidence=3, entities=("Shopify",), attributes={"sentiment": {"urgency": 0.7}}),
        memory("m3", MemoryType.PROBLEM, "The customer's Shopify integration failed with a timeout.",
               days=6, entities=("Shopify",)),
        memory("m4", MemoryType.PREFERENCE, "Please contact the customer on WhatsApp.", days=4),
        memory("m5", MemoryType.BEHAVIOR, "The customer uses the Campaign Builder feature.",
               days=20, importance=0.3),
    ]


def answer_for(question: str, memories: list[MemoryView], **kwargs):
    return compose(analysis=analyze(question), memories=memories, **kwargs)


def test_why_builds_a_causal_window(story):
    result = answer_for("Why did this customer downgrade?", story)
    assert result.strategy == "why:causal_window"
    assert "downgraded" in result.text
    assert "Shopify" in result.text
    assert result.facts["trigger_memory_id"] == "m1"
    assert result.facts["contributing_memories"] >= 2
    assert "m1" in result.evidence


def test_why_without_a_trigger_says_so(story):
    problems_only = [item for item in story if item.type is MemoryType.PROBLEM]
    result = answer_for("Why did this customer cancel?", problems_only)
    assert result.strategy == "why:no_trigger"
    assert "no memory of a cancel" in result.text.lower()


def test_problems_lists_open_issues_and_repetition(story):
    result = answer_for("What problems has this customer experienced?", story)
    assert result.strategy == "problems:list"
    assert "Two open problems" in result.text
    assert "reported 3 times" in result.text
    assert result.facts["open_problems"] == 2


def test_preferences_and_goals_have_their_own_answers(story):
    preferences = answer_for("How should we contact them?", story)
    assert preferences.strategy == "preferences:list"
    assert "WhatsApp" in preferences.text

    goals = answer_for("What are they trying to achieve?", story)
    assert goals.strategy == "goals:list:none"


def test_risk_is_scored_from_signals(story):
    result = answer_for("Are they at risk of churning?", story)
    assert result.strategy == "risk:scored"
    assert result.facts["band"] in ("moderate", "high")
    assert result.facts["signals"]


def test_counting_uses_evidence_counts(story):
    result = answer_for("How many times did Shopify fail?", story)
    assert result.strategy == "how_many:count"
    assert result.facts["occurrences"] >= 4


def test_when_reports_dates(story):
    result = answer_for("When did they downgrade?", story)
    assert result.strategy == "when:memory_dates"
    assert "yesterday" in result.text or "days ago" in result.text


def test_summary_is_sectioned(story):
    result = answer_for("Tell me about this customer", story)
    assert result.strategy == "summary:sections"
    assert "Open problems:" in result.text
    assert "Preferences:" in result.text


def test_empty_memory_never_invents_an_answer():
    result = answer_for("Why did this customer downgrade?", [])
    assert result.confidence <= 0.2
    assert "not enough memory" in result.text.lower()
    assert result.evidence == []


def test_answers_only_cite_supplied_memories(story):
    result = answer_for("What problems has this customer experienced?", story)
    assert set(result.evidence) <= {item.id for item in story}


def test_customer_name_is_used_when_known(story):
    result = answer_for("Tell me about this customer", story, customer_name="John")
    assert "John" in result.text


def test_answers_are_deterministic(story):
    first = answer_for("Why did this customer downgrade?", story)
    second = answer_for("Why did this customer downgrade?", story)
    assert first.text == second.text
    assert first.confidence == second.confidence
