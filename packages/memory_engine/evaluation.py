"""Scoring retrieval against questions whose right answers are known.

"Does retrieval work?" is not answerable by looking at it. It is answerable by asking it
questions whose answers you already know and counting how often — and how high — the right
memory comes back. This module is the counting: pure functions over what retrieval
returned, so the metrics are the same whether the run happens in a test, the worker or a
notebook.

Two ways to say what the right answer is, because each fails differently:

* **Expected memory ids** are exact, and die when a customer is reprocessed — the same
  statement comes back under a new id.
* **Expected phrases** survive reprocessing ("shopify sync fails" still matches the new
  memory), and are looser: they match any memory that mentions every word of the phrase
  in any form (``nlp.tokenize.root``).

A case may use either or both; each expected item is scored on its own.

Metrics, all macro-averaged over cases:

* **Recall@k** — the share of a case's expected items found in the top *k*.
* **Hit@k** — the share of cases with *any* expected item in the top *k*.
* **MRR** — the mean reciprocal rank of the first expected item found (0 when none is).
* **Citation hit rate** — the share of cases where the composed answer actually *cited* an
  expected memory. Retrieval can rank the right memory third and the answer still ignore
  it; this is the number that says whether the user was told the right thing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from nlp.tokenize import root, surface_words, tokenize

KS: tuple[int, ...] = (1, 3, 5, 10)


@dataclass(slots=True, frozen=True)
class Retrieved:
    """One memory as retrieval returned it, in rank order."""

    id: str
    content: str
    score: float = 0.0
    strategies: tuple[str, ...] = ()
    cited: bool = False


@dataclass(slots=True)
class CaseSpec:
    case_id: str
    question: str
    expected_ids: Sequence[str] = ()
    expected_phrases: Sequence[str] = ()


@dataclass(slots=True)
class ExpectedResult:
    kind: str  # "memory" | "phrase"
    target: str
    rank: int | None  # 1-based; None when not in the retrieved list
    memory_id: str | None = None  # the memory that matched, for phrases
    cited: bool = False


@dataclass(slots=True)
class CaseResult:
    case_id: str
    question: str
    expected: list[ExpectedResult] = field(default_factory=list)
    retrieved: list[Retrieved] = field(default_factory=list)
    answer: str | None = None
    error: str | None = None

    @property
    def first_rank(self) -> int | None:
        ranks = [item.rank for item in self.expected if item.rank is not None]
        return min(ranks) if ranks else None

    def recall_at(self, k: int) -> float:
        if not self.expected:
            return 0.0
        found = sum(1 for item in self.expected if item.rank is not None and item.rank <= k)
        return found / len(self.expected)

    def hit_at(self, k: int) -> bool:
        return any(item.rank is not None and item.rank <= k for item in self.expected)

    @property
    def reciprocal_rank(self) -> float:
        first = self.first_rank
        return 1.0 / first if first else 0.0

    @property
    def cited_hit(self) -> bool:
        return any(item.cited for item in self.expected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "error": self.error,
            "first_rank": self.first_rank,
            "reciprocal_rank": round(self.reciprocal_rank, 4),
            "cited_hit": self.cited_hit,
            "recall": {f"@{k}": round(self.recall_at(k), 4) for k in KS},
            "expected": [
                {
                    "kind": item.kind,
                    "target": item.target,
                    "rank": item.rank,
                    "memory_id": item.memory_id,
                    "cited": item.cited,
                }
                for item in self.expected
            ],
            "retrieved": [
                {
                    "id": memory.id,
                    "content": memory.content[:200],
                    "score": round(memory.score, 4),
                    "strategies": list(memory.strategies),
                    "cited": memory.cited,
                }
                for memory in self.retrieved
            ],
            "answer": self.answer,
        }


def phrase_matches(phrase: str, content: str) -> bool:
    """Whether a memory mentions every word of a phrase, in any form of each word."""
    wanted = {root(word) for word in surface_words(phrase)} or {root(word) for word in tokenize(phrase)}
    have = {root(word) for word in tokenize(content)}
    return bool(wanted) and wanted <= have


def score_case(spec: CaseSpec, retrieved: Sequence[Retrieved], *, answer: str | None = None) -> CaseResult:
    result = CaseResult(case_id=spec.case_id, question=spec.question, retrieved=list(retrieved), answer=answer)
    positions = {memory.id: index + 1 for index, memory in enumerate(retrieved)}
    by_id = {memory.id: memory for memory in retrieved}

    for memory_id in spec.expected_ids:
        rank = positions.get(memory_id)
        result.expected.append(
            ExpectedResult(
                kind="memory",
                target=memory_id,
                rank=rank,
                memory_id=memory_id if rank else None,
                cited=bool(rank and by_id[memory_id].cited),
            )
        )
    for phrase in spec.expected_phrases:
        match = next(
            ((index + 1, memory) for index, memory in enumerate(retrieved) if phrase_matches(phrase, memory.content)),
            None,
        )
        cited = any(memory.cited and phrase_matches(phrase, memory.content) for memory in retrieved)
        result.expected.append(
            ExpectedResult(
                kind="phrase",
                target=phrase,
                rank=match[0] if match else None,
                memory_id=match[1].id if match else None,
                cited=cited,
            )
        )
    return result


def aggregate(results: Sequence[CaseResult]) -> dict[str, Any]:
    scored = [result for result in results if result.error is None and result.expected]
    count = len(scored)

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    return {
        "cases": len(results),
        "scored": count,
        "errors": sum(1 for result in results if result.error is not None),
        "recall": {f"@{k}": mean([result.recall_at(k) for result in scored]) for k in KS},
        "hit": {f"@{k}": mean([1.0 if result.hit_at(k) else 0.0 for result in scored]) for k in KS},
        "mrr": mean([result.reciprocal_rank for result in scored]),
        "citation_hit_rate": mean([1.0 if result.cited_hit else 0.0 for result in scored]),
        "misses": [result.case_id for result in scored if result.first_rank is None],
    }


def compare(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    """Metric-by-metric change against a baseline run. Positive is better for all of them."""
    if not baseline:
        return None
    deltas: dict[str, Any] = {}
    for family in ("recall", "hit"):
        deltas[family] = {
            key: round(value - baseline.get(family, {}).get(key, 0.0), 4)
            for key, value in current.get(family, {}).items()
        }
    for key in ("mrr", "citation_hit_rate"):
        deltas[key] = round(current.get(key, 0.0) - baseline.get(key, 0.0), 4)
    newly_missed = sorted(set(current.get("misses", [])) - set(baseline.get("misses", [])))
    newly_found = sorted(set(baseline.get("misses", [])) - set(current.get("misses", [])))
    deltas["newly_missed"] = newly_missed
    deltas["newly_found"] = newly_found
    # A regression is any case that used to be found and no longer is — the headline
    # number can go up while a question that mattered starts failing.
    deltas["regressed"] = bool(newly_missed) or any(value < 0 for value in deltas["recall"].values())
    return deltas
