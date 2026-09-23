"""Human feedback on memories: confirm, reject or correct.

A memory the system inferred can be wrong, and the people using the dashboard are the ones
who find out. Feedback is therefore first-class: it moves confidence, can retire a memory,
and is always recorded as a version plus an audit entry — so "who decided this was wrong,
and when" is answerable months later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction, MemorySource, MemoryStatus, Sensitivity
from common.errors import NotFoundError, ValidationError
from common.logging import get_logger
from database.models import Memory, Project
from database.repositories import AuditRepository, MemoryRepository
from memory_engine.policy import classify
from webhooks import WebhookDispatcher, memory_conflict

logger = get_logger(__name__)

Verdict = Literal["confirm", "reject", "correct"]

# Confirmations move confidence towards 1 and rejections towards 0, both with diminishing
# steps so a single click cannot swing a well-evidenced memory.
CONFIRM_STEP = 0.35
REJECT_STEP = 0.5
REJECT_THRESHOLD = 0.25


@dataclass(slots=True)
class FeedbackResult:
    memory_id: str
    verdict: str
    status: str
    confidence: float
    replacement_memory_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "verdict": self.verdict,
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "replacement_memory_id": self.replacement_memory_id,
        }


class FeedbackService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        """``cleared=False`` hides memories the project's policy restricted.

        You cannot correct a memory you are not allowed to read.
        """
        self.session = session
        self.cleared = cleared
        self.memories = MemoryRepository(session, cleared=cleared)
        self.audit = AuditRepository(session)

    async def submit(
        self,
        *,
        project: Project,
        memory_id: str,
        verdict: Verdict,
        content: str | None = None,
        note: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> FeedbackResult:
        memory = await self.memories.get(memory_id, project.id)
        if memory is None:
            raise NotFoundError("Memory not found.")

        if verdict == "confirm":
            result = await self._confirm(memory, note=note)
        elif verdict == "reject":
            result = await self._reject(memory, note=note)
        elif verdict == "correct":
            if not content or len(content.strip()) < 3:
                raise ValidationError("A correction must include the corrected content.")
            result = await self._correct(
                memory, project=project, content=content.strip(), note=note
            )
        else:  # pragma: no cover - guarded by the schema
            raise ValidationError(f"Unknown verdict '{verdict}'.")

        await self.audit.record(
            action=AuditAction.MEMORY_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="memory",
            resource_id=memory.id,
            metadata={"verdict": verdict, "note": note, "result": result.as_dict()},
        )
        logger.info(
            "memory.feedback",
            memory_id=memory.id,
            verdict=verdict,
            confidence=result.confidence,
            status=result.status,
        )
        return result

    async def _confirm(self, memory: Memory, *, note: str | None) -> FeedbackResult:
        confidence = memory.confidence + (1.0 - memory.confidence) * CONFIRM_STEP
        await self.memories.apply_update(
            memory,
            confidence=confidence,
            # A human confirmation is stronger evidence than another event.
            importance=min(1.0, memory.importance + 0.05),
            reason="feedback_confirmed",
        )
        memory.meta = {**(memory.meta or {}), "human_confirmed": True, "feedback_note": note}
        await self.session.flush()
        return FeedbackResult(
            memory_id=memory.id,
            verdict="confirm",
            status=str(memory.status),
            confidence=memory.confidence,
        )

    async def _reject(self, memory: Memory, *, note: str | None) -> FeedbackResult:
        confidence = memory.confidence * (1 - REJECT_STEP)
        status = MemoryStatus.SUPERSEDED if confidence < REJECT_THRESHOLD else None
        await self.memories.apply_update(
            memory,
            confidence=confidence,
            status=status,
            reason="feedback_rejected",
        )
        memory.meta = {**(memory.meta or {}), "human_rejected": True, "feedback_note": note}
        await self.session.flush()
        return FeedbackResult(
            memory_id=memory.id,
            verdict="reject",
            status=str(memory.status),
            confidence=memory.confidence,
        )

    async def _correct(
        self, memory: Memory, *, project: Project, content: str, note: str | None
    ) -> FeedbackResult:
        """The corrected statement replaces the old one, which is kept as superseded."""
        # A correction is new text, so it is re-classified rather than inheriting the
        # sensitivity of the memory it replaces: a correction can introduce a restricted
        # term, and can equally remove the one that caused the original to be restricted.
        sensitivity, reason = classify(
            project.settings, content=content, memory_type=str(memory.type)
        )
        replacement = await self.memories.create(
            project_id=memory.project_id,
            customer_id=memory.customer_id,
            type=memory.type,
            content=content,
            importance=memory.importance,
            confidence=0.99,  # a human wrote it
            source=MemorySource.MANUAL,
            source_event_ids=list(memory.source_event_ids or []),
            first_seen_at=memory.first_seen_at,
            last_seen_at=memory.last_seen_at,
            sensitivity=Sensitivity(sensitivity),
            metadata={
                "corrects": memory.id,
                "feedback_note": note,
                **({"restricted_by": reason} if reason else {}),
            },
        )
        await self.memories.supersede(
            memory, superseded_by=replacement.id, reason="feedback_corrected"
        )
        await WebhookDispatcher(self.session).emit(
            memory_conflict(
                project_id=memory.project_id,
                memory=replacement,
                superseded_memory_id=memory.id,
                reason="A human corrected this memory.",
            )
        )
        return FeedbackResult(
            memory_id=memory.id,
            verdict="correct",
            status=str(MemoryStatus.SUPERSEDED),
            confidence=memory.confidence,
            replacement_memory_id=replacement.id,
        )
