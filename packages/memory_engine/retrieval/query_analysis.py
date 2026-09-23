"""Query understanding for retrieval.

A thin adapter over :mod:`nlp.question` so the retrieval layer has one import and the
language rules live in one place. The analysis object carries the intent, the memory types
involved, entity names, a time window and synonym-expanded keywords.
"""

from __future__ import annotations

from nlp.question import QuestionAnalysis, QuestionIntent
from nlp.question import analyze as analyze_question

# Historical name kept so existing call sites and tests read naturally.
QueryAnalysis = QuestionAnalysis


def analyze(
    query: str, *, learned_synonyms: dict[str, tuple[str, ...]] | None = None
) -> QuestionAnalysis:
    """``learned_synonyms`` is the project's own mined vocabulary, if it has any."""
    return analyze_question(query, learned_synonyms=learned_synonyms)


__all__ = ["QueryAnalysis", "QuestionAnalysis", "QuestionIntent", "analyze"]
