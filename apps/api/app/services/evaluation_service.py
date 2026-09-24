"""Retrieval evaluation: sets of known-answer questions, and runs that score retrieval.

A run asks every question through the *real* answer path — the same retrieval, ranking and
composition a customer's agent gets — with recording switched off, so evaluation neither
pollutes the query log nor counts as usage. Each run is compared against the previous
successful run of its set, and a question that used to be answered and no longer is gets
flagged even when the averages improve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import utcnow
from database.access import access_for_types, access_scope, current_access
from database.models import EvalCase, EvalRun, EvalSet, Project
from database.repositories import (
    AuditRepository,
    CustomerRepository,
    EvalRepository,
    MemoryRepository,
    QueryLogRepository,
    VocabularyRepository,
)
from memory_engine import MemoryEngine
from memory_engine.evaluation import CaseSpec, Retrieved, aggregate, compare, score_case
from memory_engine.policy import WITHHELD
from memory_engine.protocols import Embedder

logger = get_logger(__name__)

MAX_CASES_PER_SET = 500
MAX_EXPECTED = 20
# Above this a run belongs in the worker; below it, waiting for the answer is reasonable.
INLINE_LIMIT = 100


@dataclass(slots=True)
class CaseInput:
    customer_id: str
    question: str
    expected_memory_ids: list[str]
    expected_phrases: list[str]
    notes: str | None = None
    source: str = "manual"


class EvaluationService:
    def __init__(self, session: AsyncSession, embedder: Embedder | None = None) -> None:
        self.session = session
        self.embedder = embedder
        self.evals = EvalRepository(session)
        self.customers = CustomerRepository(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ sets

    async def create_set(
        self, *, project: Project, name: str, description: str | None, actor_id: str | None
    ) -> EvalSet:
        clean = name.strip()
        if not clean:
            raise ValidationError("An evaluation set needs a name.")
        if await self.evals.get_set_by_name(project.id, clean):
            raise ConflictError(f"An evaluation set named {clean!r} already exists.")
        try:
            row = await self.evals.create_set(
                project_id=project.id, name=clean, description=description, created_by=actor_id
            )
        except IntegrityError as exc:  # pragma: no cover - raced with a concurrent create
            raise ConflictError(f"An evaluation set named {clean!r} already exists.") from exc
        return row

    async def get_set(self, *, project: Project, set_id: str) -> EvalSet:
        row = await self.evals.get_set(set_id, project.id)
        if row is None:
            raise NotFoundError("Evaluation set not found.")
        return row

    async def delete_set(self, *, project: Project, set_id: str, actor_type: str, actor_id: str | None) -> None:
        row = await self.get_set(project=project, set_id=set_id)
        await self.evals.delete_set(row)
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="eval_set",
            resource_id=row.id,
            metadata={"event": "eval_set_deleted", "name": row.name},
        )

    # ----------------------------------------------------------------- cases

    async def add_cases(
        self, *, project: Project, eval_set: EvalSet, cases: list[CaseInput], cleared: bool
    ) -> list[EvalCase]:
        existing = await self.evals.cases(eval_set.id, project.id)
        if len(existing) + len(cases) > MAX_CASES_PER_SET:
            raise ValidationError(f"An evaluation set holds at most {MAX_CASES_PER_SET} cases.")

        memories = MemoryRepository(self.session, cleared=cleared)
        created: list[EvalCase] = []
        for index, case in enumerate(cases, start=1):
            label = f"Case {index}"
            question = case.question.strip()
            if not question:
                raise ValidationError(f"{label}: the question is empty.")
            phrases = [phrase.strip() for phrase in case.expected_phrases if phrase.strip()]
            ids = list(dict.fromkeys(ident.strip() for ident in case.expected_memory_ids if ident.strip()))
            if not phrases and not ids:
                raise ValidationError(
                    f"{label}: say what the right answer is — expected memory ids, expected "
                    "phrases, or both."
                )
            if len(phrases) + len(ids) > MAX_EXPECTED:
                raise ValidationError(f"{label}: at most {MAX_EXPECTED} expected items.")

            customer = await self.customers.resolve(case.customer_id, project.id)
            if customer is None:
                raise NotFoundError(f"{label}: customer {case.customer_id!r} not found.")
            if ids:
                found = {memory.id: memory for memory in await memories.get_many(ids, project.id)}
                missing = [ident for ident in ids if ident not in found or found[ident].customer_id != customer.id]
                if missing:
                    raise ValidationError(
                        f"{label}: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not "
                        f"a memory of {case.customer_id!r}."
                    )
            created.append(
                await self.evals.add_case(
                    set_id=eval_set.id,
                    project_id=project.id,
                    customer_id=customer.id,
                    question=question,
                    expected_memory_ids=ids,
                    expected_phrases=phrases,
                    notes=case.notes,
                    source=case.source,
                )
            )
        return created

    async def delete_case(self, *, project: Project, eval_set: EvalSet, case_id: str) -> None:
        if not await self.evals.delete_case(case_id=case_id, set_id=eval_set.id, project_id=project.id):
            raise NotFoundError("Case not found.")

    async def suggestions(
        self, *, project: Project, limit: int = 20, cleared: bool = True
    ) -> list[dict[str, Any]]:
        """Real questions people asked, with what retrieval returned for each.

        Not cases — a case made from retrieval's own output would only ever agree with
        itself. These are a starting point: a person picks which returned memory was
        actually the right answer, and *that* becomes the case.
        """
        logs = await QueryLogRepository(self.session).list(project_id=project.id, limit=limit * 2)
        # Reader-bound: a suggestion shows retrieved memories' words, and the answer that
        # was composed from them.
        memories = MemoryRepository(self.session, cleared=cleared)
        suggestions: list[dict[str, Any]] = []
        for log in logs:
            if log.kind != "query" or not log.customer_id:
                continue
            customer = await self.customers.get(log.customer_id, project.id)
            if customer is None:
                continue
            returned = await memories.get_many(list(log.memory_ids or [])[:8], project.id)
            order = {ident: position for position, ident in enumerate(log.memory_ids or [])}
            returned.sort(key=lambda memory: order.get(memory.id, 0))
            answer = log.answer
            if answer and not memories.sees_everything and await memories.hidden_among(
                project.id, log.memory_ids or []
            ):
                answer = WITHHELD
            suggestions.append(
                {
                    "customer_id": customer.external_id,
                    "question": log.query,
                    "asked_at": log.created_at,
                    "answer": answer,
                    "retrieved": [
                        {"id": memory.id, "type": str(memory.type), "content": memory.content}
                        for memory in returned
                    ],
                }
            )
            if len(suggestions) >= limit:
                break
        return suggestions

    # ------------------------------------------------------------------ runs

    async def start_run(
        self,
        *,
        project: Project,
        eval_set: EvalSet,
        label: str | None,
        cleared: bool,
        k: int,
        actor_id: str | None,
    ) -> EvalRun:
        cases = await self.evals.cases(eval_set.id, project.id)
        if not cases:
            raise ValidationError("This evaluation set has no cases yet — add some first.")
        baseline = await self.evals.latest_succeeded(eval_set.id)
        return await self.evals.create_run(
            set_id=eval_set.id,
            project_id=project.id,
            label=label,
            cleared=cleared,
            k=k,
            settings=await self._settings_snapshot(project),
            baseline_run_id=baseline.id if baseline else None,
            created_by=actor_id,
        )

    async def execute(self, run: EvalRun, project: Project) -> EvalRun:
        """Ask every question, score every answer, compare with the baseline."""
        if self.embedder is None:  # pragma: no cover - wiring error
            raise RuntimeError("An embedder is needed to run an evaluation.")
        await self.evals.update_run(run, status="running", started_at=utcnow())
        # Measured as the reader who started it: their clearance, and their profile's types.
        with access_scope(access_for_types((run.settings or {}).get("readable_types"))):
            return await self._execute(run, project)

    async def _execute(self, run: EvalRun, project: Project) -> EvalRun:
        engine = MemoryEngine(session=self.session, embedder=self.embedder, cleared=run.cleared)
        cases = await self.evals.cases(run.set_id, project.id)

        results = []
        for case in cases:
            spec = CaseSpec(
                case_id=case.id,
                question=case.question,
                expected_ids=list(case.expected_memory_ids or []),
                expected_phrases=list(case.expected_phrases or []),
            )
            customer = await self.customers.get(case.customer_id, project.id)
            if customer is None:
                failed = score_case(spec, [])
                failed.error = "customer no longer exists"
                results.append(failed)
                continue
            answered = await engine.answer(
                project=project, customer=customer, query=case.question, limit=run.k, record=False
            )
            retrieved = [
                Retrieved(
                    id=item["id"],
                    content=item.get("content", ""),
                    score=float(item.get("score", 0.0)),
                    strategies=tuple(item.get("retrieved_by") or ()),
                    cited=bool(item.get("cited")),
                )
                for item in answered.memories
            ]
            results.append(score_case(spec, retrieved, answer=answered.answer))

        metrics = aggregate(results)
        baseline = await self.evals.get_run(run.baseline_run_id) if run.baseline_run_id else None
        await self.evals.update_run(
            run,
            status="succeeded",
            metrics=metrics,
            results=[result.as_dict() for result in results],
            comparison=compare(metrics, baseline.metrics if baseline else None),
            finished_at=utcnow(),
        )
        logger.info(
            "eval.run_finished",
            run_id=run.id,
            cases=metrics["cases"],
            recall_at_5=metrics["recall"]["@5"],
            mrr=metrics["mrr"],
        )
        return run

    async def fail(self, run: EvalRun, error: str) -> None:
        await self.evals.update_run(run, status="failed", error=error[:1000], finished_at=utcnow())

    async def _settings_snapshot(self, project: Project) -> dict[str, Any]:
        """What retrieval was configured as, so a score can be attributed to a setting."""
        from memory_engine.engine import ENGINE_VERSION

        settings = project.settings or {}
        vocabulary = await VocabularyRepository(self.session).expansion_table(project_id=project.id)
        access = current_access()
        return {
            "engine": ENGINE_VERSION,
            "ranking_weights": settings.get("ranking_weights"),
            "consolidation_similarity": settings.get("consolidation_similarity"),
            "concept_retrieval": settings.get("concept_retrieval", True),
            "vocabulary_terms": len(vocabulary),
            # The agent profile the run was started under, so a queued run measures what
            # the key that asked for it can retrieve — not what the worker can.
            "readable_types": sorted(access.readable_types) if access.readable_types else None,
            "agent_profile": access.profile,
        }
