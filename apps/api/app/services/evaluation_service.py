"""Memory evaluation: sets of known-answer cases, and runs that score them (§28.1, §26 4.3).

Two kinds of case. A **retrieval** case asks a question through the *real* answer path —
the same retrieval, ranking and composition a customer's agent gets — with recording
switched off, so evaluation neither pollutes the query log nor counts as usage. An
**extraction** case sends an event through the real pipeline as a dry run — the same
normaliser, extractor, consolidation planner and policy as /v1/events/preview — and checks
the memories it would make against what it should and must not make.

Each run is compared against the previous run of its set under the project's own settings,
and a case that used to pass and no longer does is flagged even when the averages improve.
A **regression** runs the set twice — as configured, and under proposed settings that are
never saved — and names every case the change would break.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import validate as validate_settings
from common.enums import AuditAction, MemoryType
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
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
from memory_engine.evaluation import (
    EXPECTATION_FIELDS,
    PLANNED_ACTIONS,
    SENSITIVITIES,
    CaseSpec,
    Expectation,
    ExtractionResult,
    ExtractionSpec,
    Retrieved,
    aggregate,
    aggregate_extraction,
    compare,
    passing,
    score_case,
    score_extraction,
)
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
    question: str = ""
    expected_memory_ids: list[str] = field(default_factory=list)
    expected_phrases: list[str] = field(default_factory=list)
    notes: str | None = None
    source: str = "manual"
    kind: str = "retrieval"
    # Extraction cases (§26 4.3): the event, and what it should and must not become.
    event: dict[str, Any] | None = None
    expect: list[dict[str, Any]] = field(default_factory=list)
    forbid: list[dict[str, Any]] = field(default_factory=list)
    expect_nothing: bool = False


@dataclass(slots=True)
class Regression:
    """One set, measured as configured and under proposed settings."""

    current: EvalRun
    proposed: EvalRun
    newly_failing: list[dict[str, Any]]
    newly_passing: list[dict[str, Any]]
    summary: str

    @property
    def safe(self) -> bool:
        return not self.newly_failing


MAX_EVENT_BYTES = 16_000


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
            if case.kind == "extraction":
                customer = await self.customers.resolve(case.customer_id, project.id)
                if customer is None:
                    raise NotFoundError(f"{label}: customer {case.customer_id!r} not found.")
                event, expectations, question = _extraction_case(label, case)
                created.append(
                    await self.evals.add_case(
                        set_id=eval_set.id,
                        project_id=project.id,
                        customer_id=customer.id,
                        question=question,
                        expected_memory_ids=[],
                        expected_phrases=[],
                        notes=case.notes,
                        source=case.source,
                        kind="extraction",
                        event=event,
                        expectations=expectations,
                    )
                )
                continue
            if case.kind != "retrieval":
                raise ValidationError(f"{label}: kind is 'retrieval' or 'extraction'.")
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
        overrides: dict[str, Any] | None = None,
        baseline_run_id: str | None = None,
    ) -> EvalRun:
        cases = await self.evals.cases(eval_set.id, project.id)
        if not cases:
            raise ValidationError("This evaluation set has no cases yet — add some first.")
        if baseline_run_id is None:
            baseline = await self.evals.latest_succeeded(eval_set.id)
            baseline_run_id = baseline.id if baseline else None
        return await self.evals.create_run(
            set_id=eval_set.id,
            project_id=project.id,
            label=label,
            cleared=cleared,
            k=k,
            settings=await self._settings_snapshot(_with_overrides(project, overrides)),
            baseline_run_id=baseline_run_id,
            created_by=actor_id,
            overrides=overrides,
        )

    async def regression(
        self,
        *,
        project: Project,
        eval_set: EvalSet,
        settings: dict[str, Any],
        cleared: bool,
        k: int,
        actor_id: str | None,
    ) -> Regression:
        """Run the set as configured, then under ``settings`` — validated exactly as a save
        would be, and never saved — and name every case the change would break."""
        if not settings:
            raise ValidationError('Name the settings to try, e.g. {"consolidation_similarity": 0.4}.')
        proposed_settings = validate_settings(settings)
        size = len(await self.evals.cases(eval_set.id, project.id))
        if size > INLINE_LIMIT:
            raise ValidationError(f"A regression runs inline, for sets of up to {INLINE_LIMIT} cases.")
        current = await self.start_run(
            project=project, eval_set=eval_set, label="current settings", cleared=cleared, k=k, actor_id=actor_id
        )
        await self.execute(current, project)
        proposed = await self.start_run(
            project=project,
            eval_set=eval_set,
            label="proposed settings",
            cleared=cleared,
            k=k,
            actor_id=actor_id,
            overrides=proposed_settings,
            baseline_run_id=current.id,
        )
        await self.execute(proposed, project)

        before, after = passing(current.results or []), passing(proposed.results or [])
        labels = {result["case_id"]: result.get("question") or result.get("label") for result in proposed.results or []}
        kinds = {result["case_id"]: result.get("kind", "retrieval") for result in proposed.results or []}
        newly_failing = [
            {"case_id": case_id, "kind": kinds.get(case_id), "label": labels.get(case_id)}
            for case_id, ok in before.items()
            if ok and not after.get(case_id, False)
        ]
        newly_passing = [
            {"case_id": case_id, "kind": kinds.get(case_id), "label": labels.get(case_id)}
            for case_id, ok in after.items()
            if ok and not before.get(case_id, False)
        ]
        if newly_failing:
            summary = (
                f"{len(newly_failing)} previously passing case{'s' if len(newly_failing) != 1 else ''} "
                f"would fail{f'; {len(newly_passing)} would start passing' if newly_passing else ''}."
            )
        elif newly_passing:
            summary = f"Nothing would break, and {len(newly_passing)} failing case{'s' if len(newly_passing) != 1 else ''} would pass."
        else:
            summary = "Nothing would change: every case keeps its result."
        return Regression(current, proposed, newly_failing, newly_passing, summary)

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
        # Proposed settings are an overlay on a stand-in for the project, never written.
        view = _with_overrides(project, run.overrides)

        results: list[Any] = []
        extractions: list[ExtractionResult] = []
        for case in cases:
            if case.kind == "extraction":
                scored = await self._extraction(engine, view, case)
                extractions.append(scored)
                results.append(scored)
                continue
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
                project=view, customer=customer, query=case.question, limit=run.k, record=False
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

        metrics = aggregate([result for result in results if not isinstance(result, ExtractionResult)])
        if extractions:
            metrics["extraction"] = aggregate_extraction(extractions)
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

    async def _extraction(self, engine: MemoryEngine, project: Any, case: EvalCase) -> ExtractionResult:
        """The case's event through the real pipeline as a dry run, scored."""
        expectations = case.expectations or {}
        spec = ExtractionSpec(
            case_id=case.id,
            label=case.question,
            expect=[Expectation.from_dict(item) for item in expectations.get("expect") or []],
            forbid=[Expectation.from_dict(item) for item in expectations.get("forbid") or []],
            expect_nothing=bool(expectations.get("expect_nothing")),
        )
        customer = await self.customers.get(case.customer_id, project.id)
        if customer is None:
            failed = score_extraction(spec, [])
            failed.error = "customer no longer exists"
            return failed
        event = case.event or {}
        occurred = _moment(event.get("occurred_at"))
        explanation = await engine.preview_event(
            project=project,
            customer=customer,
            event_type=str(event.get("event_type") or ""),
            data=dict(event.get("data") or {}),
            occurred_at=occurred,
        )
        planned = [
            {
                # The statement is the case's own event, in the extractor's words; what it
                # was compared with is an existing memory, so only its id is kept.
                "content": plan.content,
                "type": plan.type,
                "action": plan.action,
                "sensitivity": plan.sensitivity,
                "entities": list(plan.entities),
                "rule": plan.rule,
                "extracted_by": plan.extracted_by,
                "closest_memory_id": plan.closest_memory_id,
            }
            for plan in explanation.memories
        ]
        return score_extraction(spec, planned, stop_reason=explanation.stop_reason)

    async def scorecard(self, *, project: Project, cleared: bool = True) -> dict[str, Any]:
        """Memory quality in one place (§26 4.3): the latest run of every set under the
        project's own settings, weighted by the cases each scored, next to the duplicate
        and consistency parts of the quality report."""
        from app.services.quality_service import QualityService  # the report reads evaluation too

        sets = await self.evals.list_sets(project.id)
        latest = await self.evals.latest_by_set([row.id for row in sets])

        def weighted(pairs: list[tuple[float | None, int]]) -> float | None:
            usable = [(value, weight) for value, weight in pairs if value is not None and weight]
            total = sum(weight for _, weight in usable)
            return round(sum(value * weight for value, weight in usable) / total, 4) if total else None

        retrieval_runs = [run for run in latest.values() if (run.metrics or {}).get("scored")]
        extraction_runs = [run for run in latest.values() if ((run.metrics or {}).get("extraction") or {}).get("scored")]
        r = [(run.metrics, int(run.metrics["scored"])) for run in retrieval_runs]
        e = [(run.metrics["extraction"], int(run.metrics["extraction"]["scored"])) for run in extraction_runs]
        report = await QualityService(self.session, cleared=cleared).report(project=project, days=30)
        parts = {component["key"]: component for component in report.get("components", [])}

        gaps: list[str] = []
        if not retrieval_runs:
            gaps.append("No retrieval run yet: add questions with known answers and run them.")
        if not extraction_runs:
            gaps.append("No extraction cases yet: add events with the memories they should make, to measure what is remembered.")
        return {
            "retrieval": {
                "questions": sum(weight for _, weight in r),
                "recall_at_5": weighted([(metrics["recall"].get("@5"), weight) for metrics, weight in r]),
                "hit_at_1": weighted([(metrics["hit"].get("@1"), weight) for metrics, weight in r]),
                "mrr": weighted([(metrics.get("mrr"), weight) for metrics, weight in r]),
                "citation_accuracy": weighted([(metrics.get("citation_hit_rate"), weight) for metrics, weight in r]),
            },
            "extraction": {
                "cases": sum(weight for _, weight in e),
                "accuracy": weighted([(metrics.get("accuracy"), weight) for metrics, weight in e]),
                "expected_recall": weighted([(metrics.get("expected_recall"), weight) for metrics, weight in e]),
                "false_memory_rate": weighted([(metrics.get("false_memory_rate"), weight) for metrics, weight in e]),
                "type_accuracy": weighted([(metrics.get("type_accuracy"), weight) for metrics, weight in e]),
                "sensitivity_accuracy": weighted([(metrics.get("sensitivity_accuracy"), weight) for metrics, weight in e]),
                "consolidation_accuracy": weighted([(metrics.get("consolidation_accuracy"), weight) for metrics, weight in e]),
            },
            "memory": {
                "duplicate_control": parts.get("duplicates", {}).get("score"),
                "consistency": parts.get("consistency", {}).get("score"),
                "freshness": parts.get("freshness", {}).get("score"),
                "quality_score": report.get("score"),
            },
            "sets": [
                {
                    "id": row.id,
                    "name": row.name,
                    "run_id": latest[row.id].id if row.id in latest else None,
                    "finished_at": latest[row.id].finished_at if row.id in latest else None,
                    "regressed": bool((latest[row.id].comparison or {}).get("regressed")) if row.id in latest else False,
                }
                for row in sets
            ],
            "gaps": gaps,
        }

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
            "keyword_match_any": settings.get("keyword_match_any", True),
            # What extraction runs read (§26 4.3).
            "min_event_importance": settings.get("min_event_importance"),
            "event_importance": settings.get("event_importance"),
            "restriction_policies": len(settings.get("restriction_policies") or []),
            "vocabulary_terms": len(vocabulary),
            # The agent profile the run was started under, so a queued run measures what
            # the key that asked for it can retrieve — not what the worker can.
            "readable_types": sorted(access.readable_types) if access.readable_types else None,
            "agent_profile": access.profile,
        }


def _with_overrides(project: Project, overrides: dict[str, Any] | None) -> Any:
    """The project with proposed settings layered on — merged the way a save would merge
    them — as a stand-in the engine reads, so nothing is ever written to the real one."""
    if not overrides:
        return project
    return SimpleNamespace(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
        settings={**(project.settings or {}), **overrides},
    )


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _extraction_case(label: str, case: CaseInput) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate an extraction case: its event, and what it should and must not become."""
    import json

    event = case.event if isinstance(case.event, dict) else None
    if not event or not str(event.get("event_type") or "").strip():
        raise ValidationError(f"{label}: an extraction case needs an event with an event_type.")
    data = event.get("data") or {}
    if not isinstance(data, dict):
        raise ValidationError(f"{label}: event.data must be an object.")
    if len(json.dumps(data, default=str)) > MAX_EVENT_BYTES:
        raise ValidationError(f"{label}: event.data is larger than {MAX_EVENT_BYTES // 1000} KB.")
    if event.get("occurred_at") and _moment(event["occurred_at"]) is None:
        raise ValidationError(f"{label}: event.occurred_at must be an ISO time.")
    types = {item.value for item in MemoryType}

    def clean(items: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for position, raw in enumerate(items or [], start=1):
            where = f"{label}, {what} {position}"
            if not isinstance(raw, dict):
                raise ValidationError(f"{where} must be an object.")
            unknown = set(raw) - set(EXPECTATION_FIELDS)
            if unknown:
                raise ValidationError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}. Fields: {', '.join(EXPECTATION_FIELDS)}.")
            expectation = Expectation.from_dict(raw)
            if not expectation.as_dict():
                raise ValidationError(f"{where} says nothing — give a type, contains, entity, sensitivity or action.")
            if expectation.type and expectation.type not in types:
                raise ValidationError(f"{where}: {expectation.type!r} is not a memory type. Types: {', '.join(sorted(types))}.")
            if expectation.sensitivity and expectation.sensitivity not in SENSITIVITIES:
                raise ValidationError(f"{where}: sensitivity is normal or restricted.")
            if expectation.action and expectation.action not in PLANNED_ACTIONS:
                raise ValidationError(f"{where}: action is one of {', '.join(PLANNED_ACTIONS)}.")
            if expectation.contains and len(expectation.contains) > 200:
                raise ValidationError(f"{where}: 'contains' is at most 200 characters.")
            out.append(expectation.as_dict())
        return out

    expect, forbid = clean(case.expect, "expectation"), clean(case.forbid, "forbidden memory")
    if case.expect_nothing and expect:
        raise ValidationError(f"{label}: a case cannot expect memories and expect nothing at once.")
    if not (expect or forbid or case.expect_nothing):
        raise ValidationError(f"{label}: say what the event should become — expect, forbid or expect_nothing.")
    if len(expect) + len(forbid) > MAX_EXPECTED:
        raise ValidationError(f"{label}: at most {MAX_EXPECTED} expectations.")
    text = " ".join(str(value) for value in data.values() if isinstance(value, str))
    question = case.question.strip() or f"{event['event_type']}: {text[:120]}".strip().rstrip(":")
    stored_event = {"event_type": str(event["event_type"]).strip(), "data": data}
    if event.get("occurred_at"):
        stored_event["occurred_at"] = str(event["occurred_at"])
    return stored_event, {"expect": expect, "forbid": forbid, "expect_nothing": bool(case.expect_nothing)}, question
