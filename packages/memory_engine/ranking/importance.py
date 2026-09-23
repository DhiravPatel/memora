"""Memory importance scoring.

The model proposes an importance; the engine adjusts it with signals the model cannot
see: how important the originating event is, how often the memory has been confirmed,
how explicit the customer was, and how business-relevant the memory type is.
"""

from __future__ import annotations

from common.enums import MemoryType

# How much a memory type matters to a SaaS business by default.
TYPE_RELEVANCE: dict[MemoryType, float] = {
    MemoryType.PROBLEM: 0.9,
    MemoryType.SUBSCRIPTION: 0.85,
    MemoryType.INTENT: 0.8,
    MemoryType.FEEDBACK: 0.75,
    MemoryType.GOAL: 0.7,
    MemoryType.PREFERENCE: 0.65,
    MemoryType.RELATIONSHIP: 0.6,
    MemoryType.FACT: 0.55,
    MemoryType.SUMMARY: 0.5,
    MemoryType.BEHAVIOR: 0.4,
}

_EXPLICIT_MARKERS = ("cancel", "refund", "churn", "angry", "frustrated", "urgent", "broken", "lost")

WEIGHTS = {
    "model": 0.40,
    "event": 0.25,
    "type": 0.20,
    "frequency": 0.10,
    "explicitness": 0.05,
}


def frequency_component(evidence_count: int) -> float:
    return min(1.0, 0.4 + 0.6 * (1 - 0.65 ** max(0, evidence_count - 1)))


def explicitness_component(content: str) -> float:
    lowered = content.lower()
    hits = sum(1 for marker in _EXPLICIT_MARKERS if marker in lowered)
    return min(1.0, 0.4 + 0.2 * hits)


def compute_importance(
    *,
    model_importance: float,
    event_importance: float,
    memory_type: MemoryType | str,
    content: str,
    evidence_count: int = 1,
    weights: dict[str, float] | None = None,
) -> float:
    try:
        parsed = MemoryType(str(memory_type))
    except ValueError:
        parsed = MemoryType.FACT

    active = {**WEIGHTS, **(weights or {})}
    components = {
        "model": max(0.0, min(1.0, model_importance)),
        "event": max(0.0, min(1.0, event_importance)),
        "type": TYPE_RELEVANCE.get(parsed, 0.5),
        "frequency": frequency_component(evidence_count),
        "explicitness": explicitness_component(content),
    }
    total_weight = sum(active.get(name, 0.0) for name in components) or 1.0
    score = sum(active.get(name, 0.0) * value for name, value in components.items()) / total_weight
    return round(max(0.0, min(1.0, score)), 4)
