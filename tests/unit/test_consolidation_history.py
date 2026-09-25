"""A memory is every statement it absorbed, not only its newest wording (§31.7).

"We will cancel if the payroll export keeps failing" once rewrote a payroll problem into a
threat; the next report — "the payroll export failed again last night" — no longer looked
like it and became a second problem. The consolidator now also compares a new report with
the wordings a memory replaced, which live in its versions.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from common.enums import MemoryStatus
from memory_engine.consolidation.consolidator import EARLIER_STATEMENTS, MemoryConsolidator
from nlp.embeddings import LocalEmbedder

EMBEDDER = LocalEmbedder(dimensions=256)


class Versions:
    def __init__(self, versions: dict[str, list[SimpleNamespace]]) -> None:
        self.versions = versions
        self.asked: list[list[str]] = []

    async def versions_for(self, ids):
        self.asked.append(list(ids))
        return {ident: self.versions.get(ident, []) for ident in ids}


def memory(ident: str, content: str, status: str = MemoryStatus.ACTIVE.value) -> SimpleNamespace:
    return SimpleNamespace(id=ident, content=content, status=status)


def version(previous: str | None, new: str) -> SimpleNamespace:
    return SimpleNamespace(previous_content=previous, new_content=new)


def match(consolidator: MemoryConsolidator, content: str, rows):
    vector = EMBEDDER.embed_sync(content)
    scored = [(row, _cosine(vector, EMBEDDER.embed_sync(row.content))) for row in rows]
    return asyncio.run(consolidator._match(content, vector, scored))


def _cosine(left, right) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


REWRITTEN = memory("m1", "The customer will cancel if the payroll export keeps failing.")
HISTORY = {
    "m1": [
        version(None, "The payroll export fails every night."),
        version("The payroll export fails every night.", REWRITTEN.content),
    ]
}


def test_a_report_meets_the_wording_a_memory_replaced():
    report = "The payroll export failed again last night."
    alone, alone_score, _ = match(MemoryConsolidator(repository=Versions(HISTORY)), report, [REWRITTEN])
    remembered, score, via = match(
        MemoryConsolidator(repository=Versions(HISTORY), embedder=EMBEDDER), report, [REWRITTEN]
    )
    assert alone is remembered is REWRITTEN
    assert via == "The payroll export fails every night."
    assert score > alone_score
    assert score >= 0.45 > alone_score, "the earlier wording clears the threshold the current one missed"


def test_the_current_wording_wins_when_it_is_closer():
    current = memory("m1", "The payroll export failed again last night.")
    history = {"m1": [version(None, "Invoices are late.")]}
    _, _, via = match(
        MemoryConsolidator(repository=Versions(history), embedder=EMBEDDER), "The payroll export failed again.", [current]
    )
    assert via is None


def test_only_active_neighbours_and_a_few_statements_each_are_read():
    repository = Versions(
        {"m1": [version(f"Statement {n} about payroll.", f"Statement {n + 1} about payroll.") for n in range(10)]}
    )
    consolidator = MemoryConsolidator(repository=repository, embedder=EMBEDDER)
    statements = asyncio.run(
        consolidator._earlier_statements([(memory("m1", "Now."), 0.5), (memory("m2", "Gone.", MemoryStatus.SUPERSEDED.value), 0.9)])
    )
    assert repository.asked == [["m1"]]
    assert len(statements) == EARLIER_STATEMENTS
    assert statements[0] == ("m1", "Statement 10 about payroll."), "newest first"
