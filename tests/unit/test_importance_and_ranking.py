"""Importance scoring and the ranking blend."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from common.enums import MemoryType
from common.time import utcnow
from memory_engine.ranking.importance import compute_importance, frequency_component
from memory_engine.ranking.ranker import MemoryRanker, RankingWeights
from memory_engine.schemas import ScoredMemory


def make_memory(**overrides):
    defaults = {
        "id": "mem_1",
        "type": MemoryType.PROBLEM,
        "content": "Customer cannot connect Shopify.",
        "importance": 0.8,
        "confidence": 0.9,
        "last_seen_at": utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_problem_outranks_page_view_behaviour():
    problem = compute_importance(
        model_importance=0.8,
        event_importance=0.8,
        memory_type=MemoryType.PROBLEM,
        content="Customer cannot connect Shopify.",
    )
    behaviour = compute_importance(
        model_importance=0.3,
        event_importance=0.05,
        memory_type=MemoryType.BEHAVIOR,
        content="Customer viewed the dashboard.",
    )
    assert problem > behaviour
    assert 0.0 <= behaviour <= 1.0


def test_repeated_evidence_increases_importance():
    once = compute_importance(
        model_importance=0.5,
        event_importance=0.5,
        memory_type=MemoryType.FACT,
        content="Customer uses Shopify.",
        evidence_count=1,
    )
    many = compute_importance(
        model_importance=0.5,
        event_importance=0.5,
        memory_type=MemoryType.FACT,
        content="Customer uses Shopify.",
        evidence_count=6,
    )
    assert many > once
    assert frequency_component(1) < frequency_component(6) <= 1.0


def test_recent_confident_memory_beats_stale_one_with_same_similarity():
    ranker = MemoryRanker(RankingWeights())
    fresh = ScoredMemory(memory=make_memory(id="fresh"), similarity=0.7)
    stale = ScoredMemory(
        memory=make_memory(id="stale", last_seen_at=utcnow() - timedelta(days=200)),
        similarity=0.7,
    )
    ranked = ranker.rank([stale, fresh])
    assert ranked[0].memory.id == "fresh"


def test_ranking_is_bounded_and_uses_all_signals():
    ranker = MemoryRanker(RankingWeights())
    candidate = ScoredMemory(
        memory=make_memory(importance=1.0, confidence=1.0),
        similarity=1.0,
        relationship_relevance=1.0,
    )
    assert ranker.score(candidate) == 1.0

    empty = ScoredMemory(
        memory=make_memory(
            importance=0.0, confidence=0.0, last_seen_at=utcnow() - timedelta(days=3650)
        )
    )
    assert ranker.score(empty) < 0.05


def test_diversify_limits_one_type_from_flooding_results():
    memories = [
        ScoredMemory(memory=make_memory(id=f"p{index}", type=MemoryType.PROBLEM), score=0.9)
        for index in range(6)
    ]
    memories.append(ScoredMemory(memory=make_memory(id="f1", type=MemoryType.FACT), score=0.1))
    kept = MemoryRanker.diversify(memories, per_type=3)
    assert sum(1 for item in kept if item.memory.type == MemoryType.PROBLEM) == 3
    assert any(item.memory.type == MemoryType.FACT for item in kept)


def test_weights_can_be_overridden_per_project():
    weights = RankingWeights.from_mapping({"similarity": 1.0, "importance": 0.0,
                                           "confidence": 0.0, "recency": 0.0, "relationship": 0.0})
    ranker = MemoryRanker(weights)
    low_similarity_but_important = ScoredMemory(
        memory=make_memory(id="important", importance=1.0), similarity=0.1
    )
    high_similarity = ScoredMemory(
        memory=make_memory(id="similar", importance=0.0, confidence=0.0), similarity=0.9
    )
    ranked = ranker.rank([low_similarity_but_important, high_similarity])
    assert ranked[0].memory.id == "similar"
