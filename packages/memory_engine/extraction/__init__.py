"""Extraction: event → candidate memories, entities and relationships.

Entity recognition itself lives in :mod:`nlp.entities`; this package owns the engine-level
policy around it (event triage, normalisation, importance floors and per-event caps).
"""

from memory_engine.extraction.filters import (
    DEFAULT_EVENT_IMPORTANCE,
    base_importance,
    score_event,
    should_extract,
)
from memory_engine.extraction.memory_extractor import MemoryExtractor, summarize_types
from memory_engine.extraction.normalizer import flatten_payload, normalize_event

__all__ = [
    "DEFAULT_EVENT_IMPORTANCE",
    "MemoryExtractor",
    "base_importance",
    "flatten_payload",
    "normalize_event",
    "score_event",
    "should_extract",
    "summarize_types",
]
