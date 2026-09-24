"""Agent runs and their explanations (§26 3.4).

Every context build and every answer is recorded as a run: who asked, what the customer's
recorded state was at that moment, which memories were retrieved and why, which were cited,
and what clearance and the agent profile held back. That is enough to answer, weeks later,
"why did my agent tell the customer their Shopify issue was resolved?" — including the case
the question usually hides: the memory it relied on has *since* been corrected. Memory
versions let the explanation show each memory as the agent saw it and as it is now.

Runs are read through :class:`app.services.reader.Reader` like everything else derived
from memory: a reader who may not see a memory the run used gets its id and scores, not its
words, and not an answer composed from it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent_policy import (
    RunExplanationOut,
    RunMemoryOut,
    RunOut,
    RunSummaryOut,
)
from app.services.guardrail_service import GuardrailService
from app.services.reader import Reader
from app.services.state_views import snapshot_out
from common.errors import NotFoundError
from common.time import ensure_utc
from database.models import Customer, Project, QueryLog
from database.repositories import (
    AgentSessionRepository,
    CustomerRepository,
    CustomerSnapshotRepository,
    QueryLogRepository,
)
from memory_engine.policy import WITHHELD

# Checks this close to a run, for the same customer, are shown with it when the run is
# not part of a session — the agent asked, then acted.
CHECK_WINDOW_SECONDS = 15 * 60
SCORE_KEYS = (
    "similarity",
    "keyword_score",
    "concept_score",
    "matched_concepts",
    "recency",
    "relationship_relevance",
    "importance",
    "confidence",
)


async def session_for(
    session: AsyncSession, project_id: str, customer_id: str, session_id: str | None
) -> Any:
    """The agent session a run or check is filed under — which must be this customer's."""
    if not session_id:
        return None
    found = await AgentSessionRepository(session).get(session_id, project_id)
    if found is None or found.customer_id != customer_id:
        raise NotFoundError(f"Agent session '{session_id}' not found for this customer.")
    return found


class RunService:
    def __init__(self, session: AsyncSession, *, cleared: bool) -> None:
        self.session = session
        self.cleared = cleared
        self.reader = Reader(session, cleared=cleared)
        self.logs = QueryLogRepository(session)
        self.customers = CustomerRepository(session)
        self.snapshots = CustomerSnapshotRepository(session)
        self.sessions = AgentSessionRepository(session)

    # ------------------------------------------------------------------ reads

    async def list(
        self,
        *,
        project: Project,
        customer: Customer | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunSummaryOut], int]:
        rows, total = await self.logs.runs(
            project_id=project.id,
            customer_id=customer.id if customer else None,
            agent=agent,
            session_id=session_id,
            kind=kind,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        hidden = await self.reader.hidden(project.id, {i for row in rows for i in row.memory_ids or []})
        external = await self._external_ids(project, {row.customer_id for row in rows if row.customer_id})
        return [self._summary(row, hidden, external) for row in rows], total

    async def get(self, *, project: Project, run_id: str) -> RunOut:
        row = await self._row(project, run_id)
        hidden = await self.reader.hidden(project.id, row.memory_ids or [])
        external = await self._external_ids(project, {row.customer_id} if row.customer_id else set())
        trace = dict(row.trace or {})
        if hidden and trace.get("memories"):
            trace["memories"] = [
                {**item, "visible": item.get("memory_id") not in hidden} for item in trace["memories"]
            ]
        return RunOut(
            **self._summary(row, hidden, external).model_dump(),
            memory_ids=list(row.memory_ids or []),
            event_ids=list(row.event_ids or []),
            trace=trace,
        )

    async def explain(self, *, project: Project, run_id: str) -> RunExplanationOut:
        row = await self._row(project, run_id)
        trace = row.trace or {}
        ranked: list[dict[str, Any]] = list(trace.get("memories") or [])
        ids = [item.get("memory_id") for item in ranked if item.get("memory_id")] or list(row.memory_ids or [])
        hidden = await self.reader.hidden(project.id, ids)
        external = await self._external_ids(project, {row.customer_id} if row.customer_id else set())
        summary = self._summary(row, hidden, external)

        visible_now = {
            memory.id: memory
            for memory in await self.reader.memories.get_many([i for i in ids if i not in hidden], project.id)
        }
        versions = await self.reader.memories.versions_for([i for i in ids if i not in hidden])
        memories = [
            self._memory(item, rank, row.created_at, hidden, visible_now, versions)
            for rank, item in enumerate(ranked or [{"memory_id": i} for i in ids], start=1)
        ]

        state_then = None
        if row.snapshot_id:
            snapshot = await self.snapshots.get(row.snapshot_id, project.id)
            if snapshot is not None:
                state_then = snapshot_out(snapshot, cleared=self.cleared)

        checks = await self._checks(project, row)
        held_back = {
            "withheld": int(trace.get("withheld") or 0),
            "cleared": trace.get("cleared", row.cleared),
            "readable_types": trace.get("readable_types"),
            "profile": trace.get("profile"),
            "hidden_from_you": len(hidden),
            "dropped_by_budget": list(trace.get("dropped_by_budget") or []),
        }
        return RunExplanationOut(
            run=summary,
            narrative=_narrative(row, summary, memories, held_back, state_then, checks),
            memories=memories,
            held_back=held_back,
            state_then=state_then,
            checks=checks,
        )

    # ---------------------------------------------------------------- pieces

    async def _row(self, project: Project, run_id: str) -> QueryLog:
        row = await self.logs.get(run_id, project.id)
        if row is None:
            raise NotFoundError(f"Run '{run_id}' not found.")
        return row

    async def _external_ids(self, project: Project, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        return {customer.id: customer.external_id for customer in await self.customers.get_many(list(ids), project.id)}

    def _summary(self, row: QueryLog, hidden: frozenset[str], external: dict[str, str]) -> RunSummaryOut:
        trace = row.trace or {}
        ranked = trace.get("memories") or []
        used_hidden = bool(hidden & set(row.memory_ids or []))
        return RunSummaryOut(
            id=row.id,
            kind=row.kind,
            customer_id=external.get(row.customer_id, row.customer_id) if row.customer_id else None,
            query=row.query,
            # An answer is composed from the memories it used, so it can quote one the
            # reader may not see.
            answer=WITHHELD if (row.answer and used_hidden) else row.answer,
            agent=row.agent,
            session_id=row.session_id,
            api_key_id=row.api_key_id,
            snapshot_id=row.snapshot_id,
            memory_count=len(row.memory_ids or []),
            cited_count=sum(1 for item in ranked if item.get("cited")),
            withheld=int(trace.get("withheld") or 0),
            latency_ms=row.latency_ms or 0,
            created_at=row.created_at,
        )

    @staticmethod
    def _memory(
        item: dict[str, Any],
        rank: int,
        at: datetime,
        hidden: frozenset[str],
        now: dict[str, Any],
        versions: dict[str, list[Any]],
    ) -> RunMemoryOut:
        ident = str(item.get("memory_id"))
        base = RunMemoryOut(
            id=ident,
            rank=int(item.get("rank") or rank),
            type=item.get("type"),
            score=float(item.get("score") or 0.0),
            strategies=list(item.get("strategies") or []),
            cited=bool(item.get("cited")),
            scores={key: item[key] for key in SCORE_KEYS if key in item},
        )
        if ident in hidden:
            base.visible = False
            base.content_then = WITHHELD
            base.content_now = WITHHELD
            return base

        history = versions.get(ident, [])
        moment = ensure_utc(at)
        before = [version for version in history if ensure_utc(version.created_at) <= moment]
        after = [version for version in history if ensure_utc(version.created_at) > moment]
        current = now.get(ident)
        base.content_then = before[-1].new_content if before else (current.content if current and not after else None)
        base.content_now = current.content if current is not None else None
        base.status_now = str(current.status) if current is not None else "deleted"
        base.changed_since = [
            {
                "at": version.created_at,
                "reason": version.reason,
                "content": version.new_content if version.new_content != version.previous_content else None,
            }
            for version in after
        ]
        return base

    async def _checks(self, project: Project, row: QueryLog) -> list[Any]:
        """Guardrail checks that belong with this run: the same agent session, or — outside
        a session — the same agent and customer within a few minutes either side. Another
        agent's checks on the same customer are its business, not this run's."""
        guardrails = GuardrailService(self.session, cleared=self.cleared)
        if row.session_id:
            found, _ = await guardrails.checks.list(project_id=project.id, session_id=row.session_id, limit=20)
        elif row.customer_id and row.agent:
            window = timedelta(seconds=CHECK_WINDOW_SECONDS)
            found, _ = await guardrails.checks.list(
                project_id=project.id,
                customer_id=row.customer_id,
                agent=row.agent,
                since=row.created_at - window,
                until=row.created_at + window,
                limit=20,
            )
        else:
            found = []
        return [await guardrails.stored_check_out(project, check) for check in found]


def _narrative(
    row: QueryLog,
    summary: RunSummaryOut,
    memories: list[RunMemoryOut],
    held_back: dict[str, Any],
    state_then: Any,
    checks: list[Any],
) -> list[str]:
    """The explanation in sentences, each one derived from a field shown alongside it."""
    lines: list[str] = []
    who = f"{row.agent} " if row.agent else "The caller "
    what = "asked" if row.kind == "query" else "requested a briefing for"
    lines.append(f"{who}{what}: “{row.query}”.")

    cited = [memory for memory in memories if memory.cited]
    if row.kind == "query":
        if cited:
            parts = []
            for memory in cited[:3]:
                how = ", ".join(memory.strategies) or "ranking"
                words = f"“{_clip(memory.content_then)}”" if memory.visible and memory.content_then else "a memory you may not see"
                parts.append(f"#{memory.rank} {words} (found by {how}, score {memory.score:.2f})")
            lines.append("The answer was built from " + "; ".join(parts) + ".")
        else:
            lines.append("The answer cited no memory — it was composed from what the retrieval returned, or said nothing was known.")
    elif memories:
        lines.append(f"The agent was handed {len(memories)} memories, ranked by relevance to the task.")

    changed = [memory for memory in memories if memory.visible and memory.changed_since]
    for memory in changed[:3]:
        latest = memory.changed_since[-1]
        verb = "was corrected" if "correct" in str(latest.get("reason", "")) else f"changed ({latest.get('reason')})"
        lines.append(
            f"Memory #{memory.rank} {verb} after this run"
            + (f": it now reads “{_clip(memory.content_now)}”." if memory.content_now else ".")
        )
    gone = [memory for memory in memories if memory.visible and memory.status_now and memory.status_now != "active"]
    for memory in gone[:3]:
        if memory not in changed:
            lines.append(f"Memory #{memory.rank} is now {memory.status_now}.")

    if held_back.get("withheld"):
        who_asked = f"the {held_back['profile']} profile" if held_back.get("profile") else "the caller's clearance"
        lines.append(f"{held_back['withheld']} of this customer's memories were not visible to {who_asked} when it asked.")
    if held_back.get("dropped_by_budget"):
        lines.append(f"{len(held_back['dropped_by_budget'])} retrieved memories were dropped to fit the token budget.")
    if state_then is not None:
        state = f", lifecycle {state_then.state}" if state_then.state else ""
        band = f" ({state_then.health_band})" if state_then.health_band else ""
        score = f"{state_then.health_score:.0f}" if state_then.health_score is not None else "unknown"
        lines.append(
            f"At the time the customer's recorded state was health {score}{band}{state}, "
            f"with {state_then.open_problems} open problem{'s' if state_then.open_problems != 1 else ''}."
        )
    for check in checks[:3]:
        lines.append(f"Guardrail check: {check.action} → {check.decision}. {check.summary}")
    if summary.answer == WITHHELD:
        lines.append("The answer itself is withheld from you: it drew on a memory you may not see.")
    return lines


def _clip(text: str | None, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
