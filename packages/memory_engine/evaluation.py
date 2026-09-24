"""Scoring memory against cases whose right answers are known — retrieval and extraction.

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
            "kind": "retrieval",
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
    extraction, before = current.get("extraction"), baseline.get("extraction")
    if extraction and before:
        newly_failing = sorted(set(extraction.get("failing", [])) - set(before.get("failing", [])))
        deltas["extraction"] = {
            "accuracy": round((extraction.get("accuracy") or 0.0) - (before.get("accuracy") or 0.0), 4),
            "false_memory_rate": round(
                (extraction.get("false_memory_rate") or 0.0) - (before.get("false_memory_rate") or 0.0), 4
            ),
            "newly_failing": newly_failing,
            "newly_passing": sorted(set(before.get("failing", [])) - set(extraction.get("failing", []))),
        }
        deltas["regressed"] = deltas["regressed"] or bool(newly_failing)
    return deltas


# ------------------------------------------------------------ extraction (§26 4.3)
#
# Retrieval can only find what was remembered. An extraction case checks the remembering:
# an event, and the memories it should — and must not — become, run through the real
# pipeline without writing (the same dry run as /v1/events/preview). "Should" is a set of
# expectations, each naming any of a memory's type, its words (matched like an expected
# phrase), an entity it names, its sensitivity and what consolidation did with it.

EXPECTATION_FIELDS = ("type", "contains", "entity", "sensitivity", "action")
PLANNED_ACTIONS = ("create", "merge", "update", "conflict", "ignore")
SENSITIVITIES = ("normal", "restricted")


@dataclass(slots=True, frozen=True)
class Expectation:
    type: str | None = None
    contains: str | None = None
    entity: str | None = None
    sensitivity: str | None = None
    action: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Expectation:
        return cls(**{name: (str(raw[name]).strip() or None) if raw.get(name) is not None else None for name in EXPECTATION_FIELDS})

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in EXPECTATION_FIELDS if getattr(self, name) is not None}

    def matches(self, planned: dict[str, Any]) -> bool:
        if self.type and str(planned.get("type")) != self.type:
            return False
        if self.contains and not phrase_matches(self.contains, str(planned.get("content") or "")):
            return False
        if self.entity and self.entity.lower() not in {str(name).lower() for name in planned.get("entities") or []}:
            return False
        if self.sensitivity and planned.get("sensitivity") != self.sensitivity:
            return False
        return not (self.action and planned.get("action") != self.action)

    def says(self, planned: dict[str, Any]) -> bool:
        """Whether a planned memory says the expected words, whatever else is wrong with it."""
        return not self.contains or phrase_matches(self.contains, str(planned.get("content") or ""))

    def differences(self, planned: dict[str, Any]) -> list[str]:
        found: list[str] = []
        if self.type and str(planned.get("type")) != self.type:
            found.append(f"typed {planned.get('type')}, expected {self.type}")
        if self.entity and self.entity.lower() not in {str(name).lower() for name in planned.get("entities") or []}:
            found.append(f"does not name {self.entity}")
        if self.sensitivity and planned.get("sensitivity") != self.sensitivity:
            found.append(f"{planned.get('sensitivity')}, expected {self.sensitivity}")
        if self.action and planned.get("action") != self.action:
            found.append(f"would {planned.get('action')}, expected {self.action}")
        return found

    def describe(self) -> str:
        parts = [f"a {self.type} memory" if self.type else "a memory"]
        if self.contains:
            parts.append(f"saying “{self.contains}”")
        if self.entity:
            parts.append(f"naming {self.entity}")
        if self.sensitivity:
            parts.append(self.sensitivity)
        if self.action:
            parts.append(f"that consolidation would {self.action}")
        return " ".join(parts)


@dataclass(slots=True)
class ExtractionSpec:
    case_id: str
    label: str
    expect: Sequence[Expectation] = ()
    forbid: Sequence[Expectation] = ()
    expect_nothing: bool = False


@dataclass(slots=True)
class ExtractionResult:
    case_id: str
    label: str
    planned: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    expected: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[int] = field(default_factory=list)
    expect_nothing: bool = False
    error: str | None = None

    @property
    def passed(self) -> bool:
        if self.error is not None:
            return False
        if self.expect_nothing and self.planned:
            return False
        return all(item["matched"] for item in self.expected) and not any(item["violated_by"] for item in self.forbidden)

    @property
    def false_memory(self) -> bool:
        return any(item["violated_by"] for item in self.forbidden) or (self.expect_nothing and bool(self.planned))

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": "extraction",
            "label": self.label,
            "passed": self.passed,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "expect_nothing": self.expect_nothing,
            "planned": self.planned,
            "expected": self.expected,
            "forbidden": self.forbidden,
            "unexpected": self.unexpected,
        }


def score_extraction(spec: ExtractionSpec, planned: Sequence[dict[str, Any]], *, stop_reason: str | None = None) -> ExtractionResult:
    """What the event became, against what it should (and must not) become."""
    plans = [dict(item) for item in planned]
    result = ExtractionResult(
        case_id=spec.case_id, label=spec.label, planned=plans, stop_reason=stop_reason, expect_nothing=spec.expect_nothing
    )
    used: set[int] = set()
    for expectation in spec.expect:
        index = next((position for position, plan in enumerate(plans) if expectation.matches(plan)), None)
        near = None
        if index is None:
            # The memory that says the words, and what was wrong with it — "typed fact,
            # expected problem" is the answer a person is looking for.
            saying = next((plan for plan in plans if expectation.contains and expectation.says(plan)), None)
            if saying is not None:
                near = "; ".join(expectation.differences(saying)) or None
            elif expectation.contains:
                near = "nothing said it" if plans else (stop_reason or "nothing was extracted")
        else:
            used.add(index)
        result.expected.append(
            {
                "expectation": expectation.as_dict(),
                "described": expectation.describe(),
                "matched": index is not None,
                "memory_index": index,
                "near_miss": near,
            }
        )
    for expectation in spec.forbid:
        violated = [position for position, plan in enumerate(plans) if expectation.matches(plan)]
        result.forbidden.append(
            {"expectation": expectation.as_dict(), "described": expectation.describe(), "violated_by": violated}
        )
        used.update(violated)
    result.unexpected = [position for position in range(len(plans)) if position not in used]
    return result


def aggregate_extraction(results: Sequence[ExtractionResult]) -> dict[str, Any]:
    """Accuracy, recall of what was expected, the false-memory rate, and — for the
    statements that were extracted at all — whether they got the right type, sensitivity
    and consolidation."""
    scored = [result for result in results if result.error is None]

    def share(hits: int, total: int) -> float | None:
        return round(hits / total, 4) if total else None

    expectations = [item for result in scored for item in result.expected]
    attribute: dict[str, list[bool]] = {"type": [], "sensitivity": [], "action": []}
    for result in scored:
        for item in result.expected:
            expectation = Expectation.from_dict(item["expectation"])
            saying = next((plan for plan in result.planned if expectation.contains and expectation.says(plan)), None)
            if saying is None:
                continue  # never extracted: a recall miss, not a type error
            for name in attribute:
                wanted = getattr(expectation, name)
                if wanted:
                    got = saying.get("type" if name == "type" else name)
                    attribute[name].append(str(got) == wanted)
    return {
        "cases": len(results),
        "scored": len(scored),
        "errors": sum(1 for result in results if result.error is not None),
        "passed": sum(1 for result in scored if result.passed),
        "accuracy": share(sum(1 for result in scored if result.passed), len(scored)),
        "expected_recall": share(sum(1 for item in expectations if item["matched"]), len(expectations)),
        "false_memory_rate": share(sum(1 for result in scored if result.false_memory), len(scored)),
        "type_accuracy": share(sum(attribute["type"]), len(attribute["type"])),
        "sensitivity_accuracy": share(sum(attribute["sensitivity"]), len(attribute["sensitivity"])),
        "consolidation_accuracy": share(sum(attribute["action"]), len(attribute["action"])),
        "failing": [result.case_id for result in results if not result.passed],
    }


def passing(results: Sequence[dict[str, Any]]) -> dict[str, bool]:
    """Pass or fail per case, whatever its kind — what a regression is measured in. A
    retrieval case passes when an expected memory came back at all; an extraction case
    when every expectation held."""
    verdicts: dict[str, bool] = {}
    for result in results:
        if result.get("kind") == "extraction":
            verdicts[result["case_id"]] = bool(result.get("passed"))
        else:
            verdicts[result["case_id"]] = result.get("error") is None and result.get("first_rank") is not None
    return verdicts
