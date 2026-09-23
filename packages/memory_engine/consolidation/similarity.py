"""Similarity helpers used to find the memory a new statement belongs to."""

from __future__ import annotations

import math
from collections.abc import Sequence

from common.text import content_hash, jaccard


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def text_similarity(left: str, right: str) -> float:
    """Token overlap; a cheap sanity check on top of vector similarity."""
    return jaccard(left, right)


def is_exact_duplicate(left: str, right: str) -> bool:
    return content_hash(left) == content_hash(right)


def combined_similarity(vector_similarity: float, left: str, right: str) -> float:
    """Blend vector and lexical similarity.

    Embeddings alone happily rate two different problems as near-identical because they
    share vocabulary; requiring some lexical agreement makes accidental merges rarer.
    """
    lexical = text_similarity(left, right)
    return round(0.75 * vector_similarity + 0.25 * lexical, 4)
