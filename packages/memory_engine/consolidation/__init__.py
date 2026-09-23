"""Consolidation: keeping one evolving memory instead of many duplicates."""

from memory_engine.consolidation.conflict import ConflictOutcome, ConflictSide, resolve
from memory_engine.consolidation.consolidator import ConsolidationOutcome, MemoryConsolidator
from memory_engine.consolidation.rules import (
    AUTO_MERGE_SIMILARITY,
    DEFAULT_THRESHOLD,
    CandidateSnapshot,
    Decision,
    MemorySnapshot,
    decide,
    detect_contradiction,
    merge_content,
    novelty,
    recurrence_note,
)
from memory_engine.consolidation.similarity import (
    combined_similarity,
    cosine_similarity,
    is_exact_duplicate,
    text_similarity,
)

__all__ = [
    "AUTO_MERGE_SIMILARITY",
    "DEFAULT_THRESHOLD",
    "CandidateSnapshot",
    "ConflictOutcome",
    "ConflictSide",
    "ConsolidationOutcome",
    "Decision",
    "MemoryConsolidator",
    "MemorySnapshot",
    "combined_similarity",
    "cosine_similarity",
    "decide",
    "detect_contradiction",
    "is_exact_duplicate",
    "merge_content",
    "novelty",
    "recurrence_note",
    "resolve",
    "text_similarity",
]
