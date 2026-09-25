"""Agent sessions: continuity for an AI agent across separate conversations.

The loop this implements is the whole point of the feature:

1. **Open** — the agent gets the customer's accumulated memory *plus* the summaries of the
   last few conversations, so it starts knowing what was already said and settled.
2. **Turn** — each customer turn is retrieved against and, if asked, ingested as an event,
   so it becomes long-term memory through the same deterministic pipeline as everything
   else. Nothing special is written for agents; there is one memory store.
3. **Close** — the session writes a summary memory composed from what actually happened.
   That memory is what the *next* session reads at step 1.

A summary is composed from stored sentences (extractive, like every other answer here), so
closing a session can never introduce a claim the conversation did not contain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.events import EventIn
from app.services.event_service import EventService
from common.enums import (
    AgentSessionStatus,
    AuditAction,
    MemorySource,
    MemoryType,
    Sensitivity,
    TurnRole,
)
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
from database.models import AgentSession, AgentTurn, Customer, Project
from database.repositories import (
    AgentSessionRepository,
    AgentTurnRepository,
    AuditRepository,
    CustomerRepository,
    MemoryRepository,
)
from memory_engine import MemoryEngine
from memory_engine.context import CustomerContext
from memory_engine.policy import classify
from nlp.summarize import rank_sentences

logger = get_logger(__name__)

# A session that nobody has touched for this long is closed by the worker, so an agent
# that crashes mid-conversation still leaves its memory behind.
IDLE_TIMEOUT_HOURS = 12
MAX_TURNS = 500
PRIOR_SESSIONS = 3
SUMMARY_SENTENCES = 4
AGENT_EVENT_TYPE = "agent_conversation"


@dataclass(slots=True)
class SessionView:
    """A session plus whatever the caller asked to be computed alongside it."""

    session: AgentSession
    resumed: bool = False
    context: CustomerContext | None = None
    prior_sessions: list[AgentSession] = field(default_factory=list)
    turns: list[AgentTurn] = field(default_factory=list)


@dataclass(slots=True)
class TurnResult:
    session: AgentSession
    turn: AgentTurn
    context: CustomerContext | None = None
    answer: str | None = None
    answer_confidence: float | None = None
    event_id: str | None = None


class AgentService:
    def __init__(self, session: AsyncSession, engine: MemoryEngine) -> None:
        self.session = session
        self.engine = engine
        self.sessions = AgentSessionRepository(session)
        self.turns = AgentTurnRepository(session)
        self.customers = CustomerRepository(session)
        self.memories = MemoryRepository(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ open

    async def open(
        self,
        *,
        project: Project,
        customer_id: str,
        agent: str = "agent",
        external_id: str | None = None,
        channel: str | None = None,
        token_budget: int | None = None,
        metadata: dict | None = None,
        actor_id: str | None = None,
        actor_type: str = "api_key",
    ) -> SessionView:
        customer = await self._resolve_customer(project, customer_id)

        resumed = False
        session = None
        if external_id:
            session = await self.sessions.get_by_external_id(external_id, project.id)
            if session is not None:
                if session.status != AgentSessionStatus.OPEN:
                    raise ConflictError(
                        f"Session '{external_id}' has already been closed. "
                        "Open a new session with a different external_id."
                    )
                resumed = True

        if session is None:
            session = await self.sessions.create(
                project_id=project.id,
                customer_id=customer.id,
                agent=agent,
                external_id=external_id,
                channel=channel,
                metadata=metadata,
            )
            await self.audit.record(
                project_id=project.id,
                organization_id=project.organization_id,
                action=AuditAction.AGENT_SESSION,
                actor_type=actor_type,
                actor_id=actor_id,
                resource_type="agent_session",
                resource_id=session.id,
                metadata={"customer_id": customer.id, "agent": agent},
            )

        context = await self.engine.build_context(
            project=project,
            customer=customer,
            task=f"brief {agent} before it replies",
            token_budget=token_budget,
            session_id=session.id,
            agent=session.agent,
        )
        prior = await self.sessions.recent_closed(
            project_id=project.id,
            customer_id=customer.id,
            limit=PRIOR_SESSIONS,
            exclude_id=session.id,
        )
        logger.info(
            "agent.session_opened",
            session_id=session.id,
            customer_id=customer.id,
            resumed=resumed,
            prior_sessions=len(prior),
        )
        return SessionView(session=session, resumed=resumed, context=context, prior_sessions=prior)

    # ------------------------------------------------------------------ turn

    async def add_turn(
        self,
        *,
        project: Project,
        session_id: str,
        role: TurnRole,
        content: str,
        remember: bool | None = None,
        retrieve: bool = True,
        occurred_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> TurnResult:
        session = await self.get(project=project, session_id=session_id)
        if session.status != AgentSessionStatus.OPEN:
            raise ConflictError(f"Session '{session.id}' is {session.status}.")
        if session.turn_count >= MAX_TURNS:
            raise ValidationError(
                f"Session '{session.id}' has reached {MAX_TURNS} turns. Close it and open a new one."
            )

        customer = await self.customers.get(session.customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{session.customer_id}' not found.")

        # Default: remember what the customer said, not what the agent said back.
        should_remember = remember if remember is not None else role == TurnRole.USER

        event_id: str | None = None
        if should_remember:
            accepted = await EventService(self.session).ingest(
                project=project,
                payload=EventIn(
                    customer_id=customer.external_id,
                    event_type=AGENT_EVENT_TYPE,
                    data={
                        "message": content,
                        "role": str(role),
                        "agent": session.agent,
                        "session_id": session.id,
                    },
                    occurred_at=occurred_at,
                    source="agent",
                ),
            )
            event_id = accepted.event_id

        context: CustomerContext | None = None
        answer: str | None = None
        confidence: float | None = None
        retrieved: list[str] = []
        if retrieve and role == TurnRole.USER:
            result = await self.engine.answer(
                project=project,
                customer=customer,
                query=content,
                limit=8,
                session_id=session.id,
                agent=session.agent,
            )
            answer = result.answer
            confidence = result.confidence
            retrieved = [memory["id"] for memory in result.memories]
            context = await self.engine.build_context(
                project=project,
                customer=customer,
                query=content,
                session_id=session.id,
                agent=session.agent,
            )

        turn = await self.turns.add(
            session_id=session.id,
            project_id=project.id,
            role=role,
            content=content,
            occurred_at=occurred_at,
            event_id=event_id,
            retrieved_memory_ids=retrieved,
            metadata=metadata,
        )
        await self.sessions.touch(session, memory_ids=retrieved)
        return TurnResult(
            session=session,
            turn=turn,
            context=context,
            answer=answer,
            answer_confidence=confidence,
            event_id=event_id,
        )

    # ----------------------------------------------------------------- close

    async def close(
        self,
        *,
        project: Project,
        session_id: str,
        write_summary: bool = True,
        outcome: str | None = None,
        status: AgentSessionStatus = AgentSessionStatus.CLOSED,
        actor_id: str | None = None,
        actor_type: str = "api_key",
    ) -> tuple[AgentSession, str | None, str | None]:
        """Close a session and leave behind what the next one needs to know."""
        session = await self.get(project=project, session_id=session_id)
        if session.status != AgentSessionStatus.OPEN:
            return session, session.summary, session.summary_memory_id

        turns = await self.turns.list(session_id=session.id, project_id=project.id, limit=MAX_TURNS)
        summary = compose_summary(turns, outcome=outcome) if write_summary else None

        memory_id: str | None = None
        if summary:
            # The summary is composed from what was said in the session, so it can carry a
            # restricted term out of turns that were themselves restricted. Classify it.
            sensitivity, reason = classify(
                project.settings, content=summary, memory_type=str(MemoryType.SUMMARY)
            )
            memory = await self.memories.create(
                project_id=project.id,
                customer_id=session.customer_id,
                type=MemoryType.SUMMARY,
                content=summary,
                importance=0.55,
                confidence=0.6,
                source=MemorySource.AGENT,
                source_event_ids=[turn.event_id for turn in turns if turn.event_id][:20],
                sensitivity=Sensitivity(sensitivity),
                metadata={
                    "agent_session_id": session.id,
                    "agent": session.agent,
                    "turns": len(turns),
                    "outcome": outcome,
                    **({"restricted_by": reason} if reason else {}),
                },
            )
            memory_id = memory.id
            # The summary is what the next session is briefed from — it has to be findable.
            await self.memories.embed(memory, self.engine.embedder)

        await self.sessions.close(
            session, summary=summary, summary_memory_id=memory_id, status=status
        )
        await self.audit.record(
            project_id=project.id,
            organization_id=project.organization_id,
            action=AuditAction.AGENT_SESSION,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="agent_session",
            resource_id=session.id,
            metadata={"status": str(status), "turns": len(turns), "summary_memory_id": memory_id},
        )
        logger.info(
            "agent.session_closed",
            session_id=session.id,
            turns=len(turns),
            wrote_memory=bool(memory_id),
        )
        return session, summary, memory_id

    # ------------------------------------------------------------------ read

    async def get(self, *, project: Project, session_id: str) -> AgentSession:
        session = await self.sessions.get(session_id, project.id)
        if session is None:
            raise NotFoundError(f"Agent session '{session_id}' not found.")
        return session

    async def detail(self, *, project: Project, session_id: str) -> SessionView:
        session = await self.get(project=project, session_id=session_id)
        turns = await self.turns.list(session_id=session.id, project_id=project.id)
        prior = await self.sessions.recent_closed(
            project_id=project.id,
            customer_id=session.customer_id,
            limit=PRIOR_SESSIONS,
            exclude_id=session.id,
        )
        return SessionView(session=session, turns=turns, prior_sessions=prior)

    async def list(
        self,
        *,
        project: Project,
        customer_id: str | None = None,
        status: AgentSessionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentSession], int]:
        resolved_id: str | None = None
        if customer_id:
            customer = await self._resolve_customer(project, customer_id)
            resolved_id = customer.id
        return await self.sessions.list(
            project_id=project.id,
            customer_id=resolved_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def _resolve_customer(self, project: Project, customer_id: str) -> Customer:
        customer = await self.customers.resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
        return customer


def compose_summary(turns: list[AgentTurn], *, outcome: str | None = None) -> str | None:
    """Build the session summary from the sentences the conversation actually contained.

    Customer turns are the source: what the agent said is its own output, and summarising
    that would let another system's phrasing leak into this one's long-term memory.
    Centroid ranking picks the sentences most representative of the conversation, and the
    original order is kept so the summary reads as it happened.
    """
    customer_turns = [turn for turn in turns if turn.role == TurnRole.USER]
    if not customer_turns:
        return None

    transcript = " ".join(turn.content.strip() for turn in customer_turns if turn.content.strip())
    selected = rank_sentences(transcript, limit=SUMMARY_SENTENCES)
    if not selected:
        # Too short to rank (a one-line conversation); keep it verbatim rather than lose it.
        body = _ended(transcript)
        if not body:
            return None
    else:
        selected.sort(key=lambda item: item.index)
        body = " ".join(_ended(item.text) for item in selected)

    started = ensure_utc(customer_turns[0].occurred_at).date().isoformat()
    summary = f"In a conversation on {started}, the customer said: {body}"
    if outcome:
        summary += f" Outcome: {outcome.strip().rstrip('.')}."
    return summary


def _ended(sentence: str) -> str:
    """The sentence with one closing mark: a question keeps its "?" rather than gaining "?."."""
    words = sentence.strip()
    if not words or words[-1] in ".?!…":
        return words
    return words + "."


def idle_cutoff(now: datetime | None = None) -> datetime:
    from datetime import timedelta

    return (now or utcnow()) - timedelta(hours=IDLE_TIMEOUT_HOURS)
