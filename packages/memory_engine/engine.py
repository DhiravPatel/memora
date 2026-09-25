"""The Memory Engine.

One object that owns the whole transformation: raw event → understanding → memories →
relationships → retrievable context → answer. The API and the worker both drive it;
neither knows how any individual step works.

There is no model provider anywhere in this path. Extraction, consolidation, retrieval and
answering are all deterministic, which means the same history always produces the same
answer, the cost of an event is bounded by CPU rather than tokens, and no customer text
ever leaves the deployment.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any

from common.enums import (
    EntityType,
    EventStatus,
    GoalStatus,
    MemoryStatus,
    MemoryType,
    RelationshipType,
)
from common.logging import get_logger
from common.metrics import event_processing_latency, events_processed
from common.pii import detect as detect_pii
from common.settings import Settings, get_settings
from common.time import days_ago, ensure_utc, utcnow
from database.access import current_access
from database.models import Customer, CustomerGoal, Event, Memory, Project
from database.repositories import (
    CustomerRepository,
    CustomerSnapshotRepository,
    EntityRepository,
    EventRepository,
    GoalRepository,
    MemoryLinkRepository,
    MemoryRepository,
    QueryLogRepository,
    RelationshipRepository,
    SignalSnapshotRepository,
    UsageRepository,
    VocabularyRepository,
)
from memory_engine import decision_trace, explain
from memory_engine.analytics import (
    ActivityWindow,
    Baseline,
    GoalSnapshot,
    HealthScore,
    Recommendation,
    SignalReport,
    compute_signals,
    recommend,
)
from memory_engine.analytics import compute as compute_health
from memory_engine.analytics.signals import WINDOW_DAYS
from memory_engine.consolidation.consolidator import MemoryConsolidator
from memory_engine.context.builder import ContextBuilder, CustomerContext
from memory_engine.explain import EntityPlan, EventExplanation, MemoryPlan
from memory_engine.extraction.memory_extractor import MemoryExtractor
from memory_engine.extraction.normalizer import normalize_event
from memory_engine.goals import tracker as goal_tracker
from memory_engine.linking import infer as infer_links
from memory_engine.policy import compile_policy
from memory_engine.policy import mask as mask_restricted
from memory_engine.protocols import Embedder
from memory_engine.ranking.importance import compute_importance
from memory_engine.ranking.ranker import MemoryRanker, RankingWeights
from memory_engine.retrieval.retriever import MemoryRetriever, RetrievalResult
from memory_engine.schemas import (
    ExtractedEntity,
    ExtractionResult,
    NormalizedEvent,
    ProcessingResult,
    ScoredMemory,
)
from nlp.answer import Answer, EventView, MemoryView, compose
from nlp.question import analyze as analyze_question

logger = get_logger(__name__)

ENGINE_VERSION = "memora-deterministic-1"

# How much of a customer's history is fed to link inference. Bounded only because a single
# customer with a million memories should not tie up one worker job indefinitely.
LINKABLE_MEMORY_LIMIT = 2000

# Memory types whose appearance can change the causal structure of a customer's story.
_LINKABLE_TYPES = {
    MemoryType.PROBLEM.value,
    MemoryType.SUBSCRIPTION.value,
    MemoryType.INTENT.value,
    MemoryType.FEEDBACK.value,
}


@dataclass(slots=True)
class _PipelineConfig:
    """A project's ingestion settings, resolved once and shared by preview and processing."""

    threshold: float
    redact: bool
    importance_overrides: dict[str, Any]
    consolidator: MemoryConsolidator


@dataclass(slots=True)
class HealthChange:
    """The result of refreshing a customer's stored health."""

    customer_id: str
    score: HealthScore
    previous_band: str | None

    @property
    def changed(self) -> bool:
        return self.previous_band != self.score.band

    @property
    def became_at_risk(self) -> bool:
        return self.score.band in ("at_risk", "critical") and self.previous_band not in (
            "at_risk",
            "critical",
        )


@dataclass(slots=True)
class GoalChange:
    """One goal that moved, and why — the worker turns these into notifications."""

    goal_id: str
    statement: str
    previous_status: str
    status: str
    progress: float
    reason: str
    memory_id: str | None = None

    @property
    def closed(self) -> bool:
        return self.status in (GoalStatus.ACHIEVED.value, GoalStatus.ABANDONED.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "statement": self.statement,
            "previous_status": self.previous_status,
            "status": self.status,
            "progress": round(self.progress, 3),
            "reason": self.reason,
            "memory_id": self.memory_id,
        }


@dataclass(slots=True)
class GoalRefresh:
    """The result of running the tracker over one customer."""

    opened: list[GoalChange] = dataclass_field(default_factory=list)
    changed: list[GoalChange] = dataclass_field(default_factory=list)

    @property
    def all(self) -> list[GoalChange]:
        return [*self.opened, *self.changed]


@dataclass(slots=True)
class QueryResult:
    answer: str
    confidence: float
    memories: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    trace: dict[str, Any]
    # The agent run this answer was recorded as; ``GET /v1/agent/runs/{id}/explain``.
    run_id: str | None = None


class MemoryEngine:
    def __init__(
        self,
        *,
        session: Any,
        embedder: Embedder,
        settings: Settings | None = None,
        cleared: bool = True,
    ) -> None:
        """``cleared=False`` builds an engine that cannot see restricted memories.

        The flag goes to the memory repository, which is the only way memories are read —
        so retrieval, context building and answer composition are all constrained by it
        without each having to remember. A caller without clearance gets answers composed
        from what they may read, not a redacted version of an answer they may not.
        """
        self.settings = settings or get_settings()
        self.session = session
        self.embedder = embedder
        self.cleared = cleared

        self.memories = MemoryRepository(session, cleared=cleared)
        self.events = EventRepository(session)
        self.customers = CustomerRepository(session)
        self.entities = EntityRepository(session)
        self.relationships = RelationshipRepository(session)
        self.query_logs = QueryLogRepository(session)
        self.customer_snapshots = CustomerSnapshotRepository(session)
        self.usage = UsageRepository(session)
        self.links = MemoryLinkRepository(session)
        self.goals = GoalRepository(session)
        self.snapshots = SignalSnapshotRepository(session)
        self.vocabulary = VocabularyRepository(session)
        self._synonyms: dict[str, tuple[str, ...]] | None = None

        self.extractor = MemoryExtractor()
        self.context_builder = ContextBuilder(
            token_budget=self.settings.memory_context_token_budget
        )

    # ------------------------------------------------------------- ingestion

    def _configure(self, project: Project) -> _PipelineConfig:
        """Everything a project's settings decide about ingestion, resolved once.

        Built here rather than inline so the dry-run and the real pipeline cannot be
        configured differently — a preview run against a different threshold than the one
        that will actually apply is worse than no preview at all.
        """
        project_settings = project.settings or {}
        return _PipelineConfig(
            threshold=float(
                project_settings.get(
                    "min_event_importance", self.settings.memory_min_event_importance
                )
            ),
            redact=bool(
                project_settings.get("pii_redaction_enabled", self.settings.pii_redaction_enabled)
            ),
            importance_overrides=project_settings.get("event_importance") or {},
            consolidator=MemoryConsolidator(
                repository=self.memories,
                policy=compile_policy(project_settings.get("restriction_policies")),
                similarity_threshold=float(
                    project_settings.get(
                        "consolidation_similarity", self.settings.memory_consolidation_similarity
                    )
                ),
                decay_days=int(
                    project_settings.get("decay_days", self.settings.memory_default_decay_days)
                ),
                embedder=self.embedder,
            ),
        )

    async def preview_event(
        self,
        *,
        project: Project,
        customer: Customer,
        event_type: str,
        data: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> EventExplanation:
        """What this event *would* do, without doing any of it.

        Every decision here comes from the same code the real pipeline runs — the same
        normaliser, the same extractor, the same consolidation planner, the same policy —
        so the preview cannot quietly disagree with what happens on send. The only thing
        it does not do is write.

        There is no event row, so a synthetic id is used for the normaliser's benefit and
        appears nowhere else.
        """
        started = time.perf_counter()
        config = self._configure(project)
        moment = occurred_at or utcnow()

        def flatten(redact: bool) -> NormalizedEvent:
            return normalize_event(
                event_id="evt_preview",
                project_id=project.id,
                customer_id=customer.id,
                event_type=event_type,
                data=data,
                occurred_at=moment,
                importance_overrides=config.importance_overrides,
                redact_pii=redact,
            )

        # Flattened twice when redaction is on, so the preview can name what was removed.
        # A dry-run is the one place where paying for that is obviously worth it.
        raw = flatten(redact=False)
        normalized = flatten(redact=True) if config.redact else raw
        findings = detect_pii(raw.text) if config.redact else []

        explanation = EventExplanation(
            event_type=event_type,
            customer_id=customer.external_id,
            text=normalized.text,
            text_length=len(normalized.text),
            redacted=bool(findings),
            redactions=[{"kind": finding.kind, "count": finding.count} for finding in findings],
            importance=normalized.importance,
            threshold=config.threshold,
        )

        if not normalized.text.strip():
            # The commonest cause of "nothing happened", and the least obvious: the
            # payload was valid JSON with no field the engine reads as prose.
            explanation.stop_reason = explain.NO_TEXT
            explanation.stop_code = "no_text"
            explanation.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            return explanation

        if normalized.importance < config.threshold:
            explanation.stop_reason = explain.below_threshold(
                normalized.importance, config.threshold
            )
            explanation.stop_code = "below_threshold"
            explanation.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            return explanation

        known_entities, _ = await self.entities.list(project_id=project.id, limit=60)
        extraction = self.extractor.extract(
            normalized,
            customer_name=customer.name,
            known_entities=[entity.name for entity in known_entities],
        )

        explanation.entities = await self._preview_entities(project=project, extraction=extraction)

        for candidate in extraction.memories:
            importance = compute_importance(
                model_importance=candidate.importance,
                event_importance=normalized.importance,
                memory_type=candidate.type,
                content=candidate.content,
            )
            vector = await self.embedder.embed_one(candidate.content)
            planned = await config.consolidator.plan(
                candidate=candidate, event=normalized, vector=vector
            )
            verdict = config.consolidator.policy.evaluate(
                content=candidate.content, memory_type=str(candidate.type)
            )

            # The planner deliberately finds an exact duplicate regardless of clearance —
            # the writer has to, or it would create a second copy of a restricted memory.
            # A *reader* must not see through that, so the target is masked here.
            target = planned.target
            hidden = target is not None and not self.memories.can_see(target)
            explanation.memories.append(
                MemoryPlan(
                    content=candidate.content,
                    type=str(candidate.type),
                    action=planned.action.value,
                    reason=planned.reason,
                    importance=importance,
                    confidence=candidate.confidence,
                    similarity=planned.similarity,
                    rule=planned.signals.get("rule"),
                    closest_memory_id=None if hidden or target is None else target.id,
                    closest_content=(
                        mask_restricted(None) if hidden else (target.content if target else None)
                    ),
                    sensitivity="restricted" if verdict.restricted else "normal",
                    restricted_by=verdict.reason,
                    extracted_by=candidate.rule or candidate.source or None,
                    entities=list(candidate.entity_names or []),
                )
            )

        explanation.would_process = True
        explanation.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return explanation

    async def _preview_entities(
        self, *, project: Project, extraction: ExtractionResult
    ) -> list[EntityPlan]:
        """Which mentioned things the project already knows, without creating any."""
        extracted = [
            entity for entity in extraction.entities if entity.type is not EntityType.CUSTOMER
        ]
        if not extracted:
            return []
        existing = await self.entities.find_by_names(
            project_id=project.id, names=[entity.name for entity in extracted]
        )
        by_name = {entity.name.lower(): entity for entity in existing}
        plans: list[EntityPlan] = []
        for entity in extracted:
            match = by_name.get(entity.name.lower())
            plans.append(
                EntityPlan(
                    name=entity.name,
                    type=str(entity.type),
                    status="existing" if match else "new",
                    entity_id=match.id if match else None,
                )
            )
        return plans

    async def process_event(self, *, event: Event, project: Project) -> ProcessingResult:
        started = time.perf_counter()
        result = ProcessingResult(event_id=event.id, processed=False)

        config = self._configure(project)
        threshold = config.threshold

        normalized = normalize_event(
            event_id=event.id,
            project_id=event.project_id,
            customer_id=event.customer_id,
            event_type=event.event_type,
            data=event.data,
            occurred_at=event.occurred_at,
            importance_overrides=config.importance_overrides,
            redact_pii=config.redact,
        )

        # The record of what happened, built as the pipeline decides it and written to the
        # event at every exit. An event that produced nothing is the one most likely to be
        # asked about, and used to be the one that said least.
        explanation = EventExplanation(
            event_type=event.event_type,
            customer_id=event.customer_id,
            text=normalized.text,
            text_length=len(normalized.text),
            importance=normalized.importance,
            threshold=threshold,
        )

        if not normalized.text.strip():
            result.skipped_reason = explain.NO_TEXT
            explanation.stop_reason = result.skipped_reason
            explanation.stop_code = "no_text"
            await self.events.mark_status(
                event.id, EventStatus.SKIPPED, error=None, outcome=explanation.as_dict(include_text=False)
            )
            events_processed.labels(status="skipped").inc()
            event_processing_latency.observe(time.perf_counter() - started)
            logger.info("event.skipped", event_id=event.id, reason="no_text")
            return result

        # Cheap triage first: most events never need to be understood at all.
        if normalized.importance < threshold:
            result.skipped_reason = explain.below_threshold(normalized.importance, threshold)
            explanation.stop_reason = result.skipped_reason
            explanation.stop_code = "below_threshold"
            await self.events.mark_status(
                event.id, EventStatus.SKIPPED, error=None, outcome=explanation.as_dict(include_text=False)
            )
            events_processed.labels(status="skipped").inc()
            event_processing_latency.observe(time.perf_counter() - started)
            logger.info("event.skipped", event_id=event.id, importance=normalized.importance)
            return result

        customer = await self.customers.get(event.customer_id, event.project_id)
        if customer is None:
            result.skipped_reason = explain.customer_missing()
            explanation.stop_reason = result.skipped_reason
            explanation.stop_code = "customer_missing"
            await self.events.mark_status(
                event.id,
                EventStatus.SKIPPED,
                error=result.skipped_reason,
                outcome=explanation.as_dict(include_text=False),
            )
            return result

        known_entities, _ = await self.entities.list(project_id=event.project_id, limit=60)
        extraction = self.extractor.extract(
            normalized,
            customer_name=customer.name,
            known_entities=[entity.name for entity in known_entities],
        )

        entity_ids = await self._persist_entities(
            project=project, customer=customer, event=normalized, extraction=extraction
        )
        result.entity_ids = entity_ids

        consolidator = config.consolidator

        for candidate in extraction.memories:
            importance = compute_importance(
                model_importance=candidate.importance,
                event_importance=normalized.importance,
                memory_type=candidate.type,
                content=candidate.content,
            )
            vector = await self.embedder.embed_one(candidate.content)
            outcome = await consolidator.consolidate(
                candidate=candidate,
                event=normalized,
                vector=vector,
                importance=importance,
            )

            memory = outcome.memory
            if outcome.created:
                result.created_memory_ids.append(memory.id)
            else:
                result.updated_memory_ids.append(memory.id)

            explanation.memories.append(
                MemoryPlan(
                    content=memory.content,
                    type=str(candidate.type),
                    action=outcome.action.value,
                    reason=outcome.reason,
                    importance=importance,
                    confidence=candidate.confidence,
                    similarity=outcome.similarity,
                    rule=outcome.signals.get("rule"),
                    memory_id=memory.id,
                    closest_memory_id=outcome.closest.id if outcome.closest else None,
                    closest_content=outcome.closest.content if outcome.closest else None,
                    sensitivity=str(memory.sensitivity),
                    restricted_by=(memory.meta or {}).get("restricted_by"),
                    extracted_by=candidate.rule or candidate.source or None,
                    entities=list(candidate.entity_names or []),
                )
            )

            # Re-embed only when consolidation rewrote the text.
            final_vector = (
                vector
                if memory.content.strip() == candidate.content.strip()
                else await self.embedder.embed_one(memory.content)
            )
            await self.memories.upsert_embedding(
                project_id=event.project_id,
                memory_id=memory.id,
                vector=final_vector,
                model=self.embedder.model,
            )
            for entity_id in self._entity_ids_for(candidate, entity_ids, extraction):
                await self.memories.link_entity(memory.id, entity_id)

        # Structure only changes when a memory that can participate in a chain changed.
        if result.created_memory_ids or result.updated_memory_ids:
            touched_types = {
                str(candidate.type) for candidate in extraction.memories
            }
            if touched_types & _LINKABLE_TYPES:
                result.relationship_ids = await self.refresh_links(
                    project=project, customer=customer
                )

        explanation.entities = [
            EntityPlan(name=entity.name, type=str(entity.type), status="touched")
            for entity in extraction.entities
        ]
        explanation.would_process = True
        explanation.duration_ms = round((time.perf_counter() - started) * 1000, 2)

        await self.customers.touch_last_event(customer, event.occurred_at)
        await self.events.mark_status(
            event.id, EventStatus.PROCESSED, outcome=explanation.as_dict(include_text=False)
        )
        await self.usage.increment(project_id=event.project_id, metric="events_processed")
        if result.created_memory_ids or result.updated_memory_ids:
            await self.usage.increment(
                project_id=event.project_id,
                metric="memories_written",
                amount=len(result.created_memory_ids) + len(result.updated_memory_ids),
            )

        result.processed = True
        result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        result.candidates_considered = len(extraction.memories)
        events_processed.labels(status="processed").inc()
        event_processing_latency.observe(time.perf_counter() - started)
        logger.info(
            "event.processed",
            event_id=event.id,
            created=len(result.created_memory_ids),
            updated=len(result.updated_memory_ids),
            entities=len(entity_ids),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return result

    def _entity_ids_for(
        self,
        candidate: Any,
        entity_ids: list[str],
        extraction: ExtractionResult,
    ) -> list[str]:
        """Link a memory to the entities its own sentence mentioned, else to the event's."""
        if not candidate.entity_names:
            return entity_ids
        wanted = {name.lower() for name in candidate.entity_names}
        mapped = [
            entity_id
            for entity_id, entity in zip(entity_ids, extraction.entities, strict=False)
            if entity.name.lower() in wanted
        ]
        return mapped or entity_ids

    async def _persist_entities(
        self,
        *,
        project: Project,
        customer: Customer,
        event: NormalizedEvent,
        extraction: ExtractionResult,
    ) -> list[str]:
        entities: list[ExtractedEntity] = list(extraction.entities)
        if not entities and not extraction.relationships:
            return []

        customer_label = customer.name or customer.external_id
        customer_entity = await self.entities.upsert(
            project_id=project.id,
            type=EntityType.CUSTOMER,
            name=customer_label,
            external_id=customer.external_id,
            metadata={"customer_id": customer.id},
        )

        stored: dict[str, str] = {customer_label.lower(): customer_entity.id}
        entity_ids: list[str] = []
        for extracted in entities:
            if extracted.type is EntityType.CUSTOMER:
                continue
            entity = await self.entities.upsert(
                project_id=project.id,
                type=extracted.type,
                name=extracted.name,
                external_id=extracted.external_id,
            )
            stored[extracted.name.lower()] = entity.id
            entity_ids.append(entity.id)

        for edge in extraction.relationships:
            source_id = stored.get(edge.source.lower(), customer_entity.id)
            target_id = stored.get(edge.target.lower())
            if not target_id or source_id == target_id:
                continue
            await self.relationships.upsert(
                project_id=project.id,
                source_entity_id=source_id,
                relationship_type=_as_relationship_type(edge.type),
                target_entity_id=target_id,
                confidence=edge.confidence,
            )

        return entity_ids

    async def refresh_health(self, *, project: Project, customer: Customer) -> HealthChange:
        """Recompute health from memory and store it on the customer row.

        Persisting it means the dashboard, the alerting path and the API all read the same
        number, and a band change can be detected without recomputing anything.
        """
        memories, _ = await self.memories.list(
            project_id=project.id, customer_id=customer.id, limit=200
        )
        event_count = await self.events.count(project.id)
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        score = compute_health(
            memories=[self._to_view(memory) for memory in memories],
            event_count=event_count,
            last_event_at=customer.last_event_at,
            distinct_features=features.get(customer.id, 0),
            weights=(project.settings or {}).get("health_weights"),
        )

        previous_band = customer.health_band
        customer.health_score = score.score
        customer.health_band = score.band
        customer.health_computed_at = score.computed_at
        await self.session.flush()

        if previous_band != score.band:
            logger.info(
                "customer.health_changed",
                customer_id=customer.id,
                previous_band=previous_band,
                band=score.band,
                score=score.score,
            )
        return HealthChange(customer_id=customer.id, score=score, previous_band=previous_band)

    async def refresh_links(self, *, project: Project, customer: Customer) -> list[str]:
        """Recompute the causal/temporal links between this customer's memories."""
        # Inference is windowed and bounded per memory, so the whole history can be handed
        # over rather than the most recent couple of hundred.
        memories, _ = await self.memories.list(
            project_id=project.id,
            customer_id=customer.id,
            status=MemoryStatus.ACTIVE,
            limit=LINKABLE_MEMORY_LIMIT,
        )
        if len(memories) < 2:
            return []
        proposals = infer_links([self._to_view(memory) for memory in memories])
        await self.links.replace_for_customer(
            project_id=project.id,
            customer_id=customer.id,
            links=[
                (
                    proposal.source_memory_id,
                    proposal.target_memory_id,
                    proposal.link_type.value,
                    proposal.confidence,
                    proposal.rationale,
                )
                for proposal in proposals
            ],
        )
        logger.info(
            "memory.links_refreshed",
            customer_id=customer.id,
            memories=len(memories),
            links=len(proposals),
        )
        return [proposal.source_memory_id for proposal in proposals]

    # -------------------------------------------------------------- foresight

    async def refresh_goals(self, *, project: Project, customer: Customer) -> GoalRefresh:
        """Open goals the customer has stated, and move the ones later events have changed.

        Runs after every processed event. Opening is cheap (a goal memory either exists or
        it does not); closing is the part that matters, because the message that completes
        a goal almost never mentions the goal.
        """
        refresh = GoalRefresh()
        memories = await self.active_memories(project=project, customer=customer, limit=200)
        if not memories:
            return refresh

        stored, _ = await self.goals.list(project_id=project.id, customer_id=customer.id, limit=100)
        views = [self._goal_view(goal) for goal in stored]

        for candidate in goal_tracker.candidates(
            memories, tracked_memory_ids=[goal.memory_id for goal in stored if goal.memory_id]
        ):
            if goal_tracker.duplicate_of(candidate, views) is not None:
                continue
            created = await self.goals.create(
                project_id=project.id,
                customer_id=customer.id,
                statement=candidate.statement,
                keywords=candidate.keywords,
                memory_id=candidate.memory_id,
                confidence=candidate.confidence,
                opened_at=candidate.stated_at,
                evidence=[
                    {
                        "kind": "stated",
                        "memory_id": candidate.memory_id,
                        "at": candidate.stated_at.isoformat(),
                    }
                ],
            )
            stored.append(created)
            views.append(self._goal_view(created))
            refresh.opened.append(
                GoalChange(
                    goal_id=created.id,
                    statement=created.statement,
                    previous_status="",
                    status=str(created.status),
                    progress=created.progress,
                    reason="stated by the customer",
                    memory_id=created.memory_id,
                )
            )

        by_id = {goal.id: goal for goal in stored}
        for view in views:
            goal = by_id[view.id]
            # A human verdict outranks the tracker; it stops moving that goal entirely.
            if goal.overridden_by or view.status.is_closed:
                continue
            transition = goal_tracker.decide(view, memories=memories)
            if transition is None:
                continue
            previous = str(goal.status)
            await self.goals.apply(
                goal,
                status=transition.status,
                progress=transition.progress,
                confidence=transition.confidence,
                evidence=transition.evidence,
                closed_reason=transition.reason if transition.closes else None,
            )
            refresh.changed.append(
                GoalChange(
                    goal_id=goal.id,
                    statement=goal.statement,
                    previous_status=previous,
                    status=str(transition.status),
                    progress=transition.progress,
                    reason=transition.reason,
                    memory_id=goal.memory_id,
                )
            )

        if refresh.all:
            logger.info(
                "goals.refreshed",
                customer_id=customer.id,
                opened=len(refresh.opened),
                changed=len(refresh.changed),
            )
        return refresh

    async def signals_for(
        self,
        *,
        project: Project,
        customer: Customer,
        health_score: float | None = None,
        memories: Sequence[MemoryView] | None = None,
    ) -> SignalReport:
        """Forecast one customer: trajectory, churn risk, expansion, and the evidence."""
        views = list(
            memories
            if memories is not None
            else await self.active_memories(project=project, customer=customer, limit=200)
        )
        now = utcnow()
        counts = await self.events.window_counts_by_customer(
            project_id=project.id,
            customer_ids=[customer.id],
            boundary=days_ago(WINDOW_DAYS),
            since=days_ago(WINDOW_DAYS * 2),
        )
        recent_events, prior_events = counts.get(customer.id, (0, 0))
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        stored_goals, _ = await self.goals.list(
            project_id=project.id, customer_id=customer.id, limit=50
        )
        snapshot = await self.snapshots.baseline(
            project_id=project.id,
            customer_id=customer.id,
            on_or_before=days_ago(WINDOW_DAYS).date(),
        )

        if health_score is None:
            health_score = (
                float(customer.health_score)
                if customer.health_score is not None
                else compute_health(
                    memories=views,
                    event_count=recent_events + prior_events,
                    last_event_at=customer.last_event_at,
                    distinct_features=features.get(customer.id, 0),
                    weights=(project.settings or {}).get("health_weights"),
                ).score
            )

        return compute_signals(
            memories=views,
            activity=ActivityWindow(
                recent_events=recent_events,
                prior_events=prior_events,
                last_event_at=customer.last_event_at,
                distinct_features=features.get(customer.id, 0),
            ),
            goals=[self._goal_snapshot(goal) for goal in stored_goals],
            health_score=health_score,
            baseline=(
                Baseline(
                    health_score=float(snapshot.health_score),
                    churn_risk=float(snapshot.churn_risk),
                    captured_at=ensure_utc(snapshot.captured_at),
                )
                if snapshot is not None
                else None
            ),
            now=now,
        )

    async def recommendations_for(
        self,
        *,
        project: Project,
        customer: Customer,
        report: SignalReport | None = None,
        memories: Sequence[MemoryView] | None = None,
        limit: int = 5,
    ) -> list[Recommendation]:
        """What to do about this customer, most urgent first."""
        views = list(
            memories
            if memories is not None
            else await self.active_memories(project=project, customer=customer, limit=200)
        )
        report = report or await self.signals_for(
            project=project, customer=customer, memories=views
        )
        stored_goals, _ = await self.goals.list(
            project_id=project.id, customer_id=customer.id, limit=50
        )
        return recommend(
            memories=views,
            report=report,
            goals=[self._goal_snapshot(goal) for goal in stored_goals],
            health_score=float(customer.health_score or 70.0),
            customer_name=customer.name,
            limit=limit,
        )

    async def record_signals(
        self, *, project: Project, customer: Customer, report: SignalReport
    ) -> None:
        """Persist today's reading so tomorrow's trajectory has something to compare with."""
        await self.snapshots.record(
            project_id=project.id,
            customer_id=customer.id,
            health_score=float(customer.health_score or 0.0),
            churn_risk=report.churn_risk,
            expansion_score=report.expansion_score,
            trajectory=str(report.trajectory),
            signals=[
                {"key": signal.key, "direction": str(signal.direction), "strength": signal.strength}
                for signal in report.signals
            ],
            at=report.computed_at,
        )

    @staticmethod
    def _goal_view(goal: CustomerGoal) -> goal_tracker.GoalView:
        return goal_tracker.GoalView(
            id=goal.id,
            statement=goal.statement,
            keywords=tuple(goal.keywords or ()),
            status=GoalStatus(str(goal.status)),
            progress=float(goal.progress or 0.0),
            opened_at=ensure_utc(goal.opened_at),
            last_signal_at=ensure_utc(goal.last_signal_at),
            memory_id=goal.memory_id,
        )

    @staticmethod
    def _goal_snapshot(goal: CustomerGoal) -> GoalSnapshot:
        return GoalSnapshot(
            id=goal.id,
            statement=goal.statement,
            status=str(goal.status),
            progress=float(goal.progress or 0.0),
            last_signal_at=ensure_utc(goal.last_signal_at),
        )

    # -------------------------------------------------------------- retrieval

    async def learned_synonyms(self, project: Project) -> dict[str, tuple[str, ...]]:
        """This project's mined vocabulary, cached for the life of the engine instance.

        One engine is built per request (and per worker job), so this is one query per
        request at most, and none at all for a project that has never been mined.
        """
        if self._synonyms is None:
            self._synonyms = await self.vocabulary.expansion_table(project_id=project.id)
        return self._synonyms

    def build_retriever(self, project: Project) -> MemoryRetriever:
        weights = RankingWeights.from_mapping(
            (project.settings or {}).get("ranking_weights"),
            RankingWeights.from_settings(self.settings),
        )
        return MemoryRetriever(
            memories=self.memories,
            entities=self.entities,
            relationships=self.relationships,
            embedder=self.embedder,
            ranker=MemoryRanker(weights),
            candidate_limit=self.settings.memory_retrieval_candidates,
        )

    async def search(
        self,
        *,
        project: Project,
        customer: Customer | None,
        query: str,
        limit: int = 10,
        types: Sequence[MemoryType] | None = None,
    ) -> RetrievalResult:
        retriever = self.build_retriever(project)
        return await retriever.retrieve(
            project_id=project.id,
            customer_id=customer.id if customer else None,
            query=query,
            limit=limit,
            types=types,
            include_concepts=bool((project.settings or {}).get("concept_retrieval", True)),
            keyword_any=bool((project.settings or {}).get("keyword_match_any", True)),
            learned_synonyms=await self.learned_synonyms(project),
        )

    async def answer(
        self,
        *,
        project: Project,
        customer: Customer,
        query: str,
        limit: int = 10,
        record: bool = True,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> QueryResult:
        """Answer a question about a customer from memory.

        ``record=False`` answers without writing a query log or counting usage — for an
        evaluation run, whose hundreds of synthetic questions are neither traffic nor
        something a customer should be billed for, and which would otherwise pollute the
        audit trail of real questions with its own.

        A recorded answer is an agent run (§26 3.4): ``session_id`` and ``agent`` tie it to
        the conversation it happened in, when there is one.
        """
        started = time.perf_counter()
        retrieval = await self.search(project=project, customer=customer, query=query, limit=limit)
        analysis = analyze_question(query, learned_synonyms=await self.learned_synonyms(project))

        views = [self._to_view(item.memory, score=item.score) for item in retrieval.memories]
        recent_events = await self.events.recent_for_customer(
            project_id=project.id, customer_id=customer.id, limit=8
        )
        event_views = [
            EventView(
                id=event.id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                importance=float(event.importance),
            )
            for event in recent_events
        ]

        composed: Answer = compose(
            analysis=analysis,
            memories=views,
            events=event_views,
            customer_name=customer.name,
        )

        cited = set(composed.evidence)
        memories_payload = [
            {
                **self._memory_payload(item.memory),
                "score": round(item.score, 4),
                "retrieved_by": sorted(item.strategies),
                "cited": item.memory.id in cited,
            }
            for item in retrieval.memories
        ]
        source_event_ids = composed.event_ids or sorted(
            {
                event_id
                for item in retrieval.memories
                if item.memory.id in cited
                for event_id in (item.memory.source_event_ids or [])
            }
        )

        latency_ms = int((time.perf_counter() - started) * 1000)
        run_id = None
        if record:
            run_id = await self._record_run(
                project=project,
                customer=customer,
                kind="query",
                query=query,
                answer=composed.text,
                ranked=retrieval.memories,
                cited=cited,
                event_ids=source_event_ids,
                latency_ms=latency_ms,
                session_id=session_id,
                agent=agent,
                extra={
                    "analysis": analysis.as_dict(),
                    "strategies": retrieval.strategies_used,
                    "answer_strategy": composed.strategy,
                    "reasoning": composed.reasoning,
                    "evidence": composed.evidence,
                    "confidence": round(composed.confidence, 3),
                    **await self._considered(project, customer, query, retrieval, given=retrieval.memories),
                },
            )
            await self.usage.increment(project_id=project.id, metric="ai_queries")

        return QueryResult(
            run_id=run_id,
            answer=composed.text,
            confidence=composed.confidence,
            memories=memories_payload,
            sources=[{"event_id": event_id} for event_id in source_event_ids],
            trace={
                "engine": ENGINE_VERSION,
                "analysis": analysis.as_dict(),
                "strategies": retrieval.strategies_used,
                "ranking": retrieval.explain(),
                "answer_strategy": composed.strategy,
                "reasoning": composed.reasoning,
                "facts": composed.facts,
                "evidence": composed.evidence,
                "latency_ms": latency_ms,
            },
        )

    async def build_context(
        self,
        *,
        project: Project,
        customer: Customer,
        query: str | None = None,
        task: str | None = None,
        limit: int = 20,
        token_budget: int | None = None,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> CustomerContext:
        """Assemble the context an external AI agent needs before it answers."""
        started = time.perf_counter()
        effective_query = query or task or "important context about this customer"
        retrieval = await self.search(
            project=project, customer=customer, query=effective_query, limit=limit
        )
        memories = await self._with_floor(project, customer, retrieval.memories, limit=limit)

        recent_events = await self.events.recent_for_customer(
            project_id=project.id, customer_id=customer.id, limit=8
        )
        edges = await self._relationship_summary(project_id=project.id, customer=customer)

        context = self.context_builder.build(
            customer={
                "id": customer.id,
                "external_id": customer.external_id,
                "name": customer.name,
                "email": customer.email,
            },
            memories=memories,
            recent_events=[
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                for event in recent_events
            ],
            relationships=edges,
            token_budget=token_budget,
        )
        included = set(context.memory_ids)
        context.run_id = await self._record_run(
            project=project,
            customer=customer,
            kind="context",
            query=effective_query,
            # What the agent was actually handed: the budget can drop the tail.
            ranked=[item for item in memories if item.memory.id in included],
            memory_ids=context.memory_ids,
            latency_ms=int((time.perf_counter() - started) * 1000),
            session_id=session_id,
            agent=agent,
            extra={
                "strategies": retrieval.strategies_used,
                "token_count": context.token_count,
                "token_budget": token_budget,
                "truncated": context.truncated,
                "dropped_by_budget": [ident for ident, why in context.left_out.items() if why == "token_budget"],
                "left_out": context.left_out,
                **await self._considered(project, customer, effective_query, retrieval, given=memories),
            },
        )
        await self.usage.increment(project_id=project.id, metric="context_requests")
        return context

    async def _considered(
        self,
        project: Project,
        customer: Customer,
        query: str,
        retrieval: RetrievalResult,
        *,
        given: Sequence[Any],
    ) -> dict[str, Any]:
        """What a run considered but did not give the agent (§26 4.4): the candidates
        ranking passed over, and the memories the question matched that no agent could
        have been given — superseded, expired, or hidden from this reader. Ids and reasons;
        content is shaped for whoever reads the trace."""
        handed = {item.memory.id for item in given}
        inactive = await self.memories.inactive_for_customer(project_id=project.id, customer_id=customer.id)
        hidden = await self.memories.hidden_for_customer(project_id=project.id, customer_id=customer.id)
        return {
            "passed_over": [item.explain() for item in retrieval.passed_over if item.item.memory.id not in handed],
            "cut": {"limit": retrieval.limit, "per_type": retrieval.per_type, "candidates": retrieval.candidate_count},
            "unseen": decision_trace.unseen(
                query,
                inactive=inactive,
                hidden=hidden,
                exclude=handed,
                asked_types=[str(kind) for kind in retrieval.analysis.types or []],
                cleared=self.cleared,
                readable_types=self.memories.readable_types,
            ),
        }

    async def _record_run(
        self,
        *,
        project: Project,
        customer: Customer,
        kind: str,
        query: str,
        ranked: Sequence[Any],
        answer: str | None = None,
        cited: set[str] | frozenset[str] = frozenset(),
        memory_ids: list[str] | None = None,
        event_ids: list[str] | None = None,
        latency_ms: int = 0,
        session_id: str | None = None,
        agent: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Store a context build or an answer as an agent run, and return its id.

        The trace is what "why did my agent say that?" needs a month later, when the
        memories may have changed: each memory's rank and the scores that put it there,
        whether the answer cited it, what the reader's clearance and profile held back,
        and the customer snapshot current at that moment.
        """
        access = current_access()
        snapshot = await self.customer_snapshots.latest(project_id=project.id, customer_id=customer.id)
        withheld = await self.memories.withheld_count(project_id=project.id, customer_id=customer.id)
        readable = self.memories.readable_types
        trace = {
            "engine": ENGINE_VERSION,
            "memories": [
                {
                    **item.explain(),
                    "rank": rank,
                    "type": str(item.memory.type),
                    "cited": item.memory.id in cited,
                }
                for rank, item in enumerate(ranked, start=1)
            ],
            "withheld": withheld,
            "cleared": self.cleared,
            "readable_types": sorted(readable) if readable is not None else None,
            "profile": access.profile,
            **(extra or {}),
        }
        log = await self.query_logs.record(
            project_id=project.id,
            customer_id=customer.id,
            kind=kind,
            query=query,
            answer=answer,
            memory_ids=memory_ids if memory_ids is not None else [item.memory.id for item in ranked],
            event_ids=event_ids,
            provider="deterministic",
            model=ENGINE_VERSION,
            latency_ms=latency_ms,
            api_key_id=access.api_key_id,
            agent=agent or access.agent,
            session_id=session_id,
            snapshot_id=snapshot.id if snapshot else None,
            cleared=self.cleared,
            trace=trace,
        )
        return log.id

    async def _with_floor(
        self, project: Project, customer: Customer, retrieved: list[Any], *, limit: int
    ) -> list[Any]:
        """What the task asked about, then the customer's most important memories.

        Retrieval's own fallback fires only when *nothing* matches. With six recall
        strategies something nearly always matches, so a vague request ("support_response")
        would come back with one loosely related memory — a context with no open problems,
        handed to an agent about to reply to that customer. Context is a briefing, not a
        search result, so it is topped up to ``limit`` with the memories that matter most,
        after the relevant ones and marked as such.
        """
        if len(retrieved) >= limit:
            return retrieved
        present = {item.memory.id for item in retrieved}
        ranker = self.build_retriever(project).ranker
        floor: list[Any] = []
        for memory in await self.memories.top_for_customer(
            project_id=project.id, customer_id=customer.id, limit=limit
        ):
            if memory.id in present:
                continue
            item = ScoredMemory(memory=memory, strategies={"floor"})
            ranker.score(item)
            floor.append(item)
        floor.sort(key=lambda item: item.score, reverse=True)
        return [*retrieved, *floor[: limit - len(retrieved)]]

    # ------------------------------------------------------------- utilities

    @staticmethod
    def _memory_payload(memory: Memory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "type": str(memory.type),
            "content": memory.content,
            "importance": round(float(memory.importance), 3),
            "confidence": round(float(memory.confidence), 3),
            "last_seen_at": memory.last_seen_at.isoformat(),
            "source_event_ids": list(memory.source_event_ids or []),
        }

    @staticmethod
    def _to_view(memory: Memory, *, score: float = 0.0) -> MemoryView:
        meta = memory.meta or {}
        return MemoryView(
            id=memory.id,
            type=MemoryType(str(memory.type)),
            content=memory.content,
            importance=float(memory.importance),
            confidence=float(memory.confidence),
            last_seen_at=memory.last_seen_at,
            first_seen_at=memory.first_seen_at,
            evidence_count=memory.evidence_count,
            source_event_ids=list(memory.source_event_ids or []),
            score=score,
            entities=list(meta.get("entity_names") or []),
            status=str(memory.status),
            attributes=meta,
        )

    async def _relationship_summary(
        self, *, project_id: str, customer: Customer
    ) -> list[dict[str, Any]]:
        label = (customer.name or customer.external_id).strip().lower()
        matches = await self.entities.find_by_names(project_id=project_id, names=[label])
        if not matches:
            return []
        # An integration and a customer can share a name; the customer node is the root.
        customer_entity = next(
            (entity for entity in matches if entity.type == EntityType.CUSTOMER), matches[0]
        )
        edges = await self.relationships.for_entities(
            project_id=project_id, entity_ids=[customer_entity.id]
        )
        if not edges:
            return []
        referenced = {edge.source_entity_id for edge in edges} | {
            edge.target_entity_id for edge in edges
        }
        entities = {
            entity.id: entity
            for entity in await self.entities.get_many(list(referenced), project_id)
        }
        summary: list[dict[str, Any]] = []
        for edge in edges:
            source = entities.get(edge.source_entity_id)
            target = entities.get(edge.target_entity_id)
            if source is None or target is None:
                continue
            summary.append(
                {
                    "source": source.name,
                    "type": str(edge.relationship_type),
                    "target": target.name,
                    "target_type": str(target.type),
                    "confidence": round(float(edge.confidence), 3),
                }
            )
        return summary

    async def active_memories(
        self, *, project: Project, customer: Customer, limit: int = 100
    ) -> list[MemoryView]:
        memories, _ = await self.memories.list(
            project_id=project.id,
            customer_id=customer.id,
            status=MemoryStatus.ACTIVE,
            limit=limit,
        )
        return [self._to_view(memory) for memory in memories]


def _as_relationship_type(value: Any) -> RelationshipType:
    try:
        return RelationshipType(str(value))
    except ValueError:
        return RelationshipType.RELATED_TO
