"""Vocabulary persistence: the mined table, the curated glossary and the rejections."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select

from common.enums import VocabularySource, VocabularyStatus
from common.errors import ConflictError
from common.ids import new_id
from common.time import utcnow
from database.models import LearnedTerm
from database.repositories.base import BaseRepository


class VocabularyRepository(BaseRepository):
    async def replace_mined(
        self,
        *,
        project_id: str,
        pairs: Sequence[tuple[str, str, float, int]],
        mined_at: datetime | None = None,
    ) -> int:
        """Swap in a freshly mined table, leaving human decisions alone.

        Mined rows are replaced wholesale — the job reads the whole corpus every time, so a
        pair that is no longer supported should disappear rather than linger because it was
        true once. Curated rows and rejections survive, and a pair a person rejected is
        never written back.
        """
        now = mined_at or utcnow()
        await self.session.execute(
            delete(LearnedTerm).where(
                LearnedTerm.project_id == project_id,
                LearnedTerm.source == VocabularySource.MINED,
                LearnedTerm.status == VocabularyStatus.ACTIVE,
            )
        )
        # Everything a person has already ruled on, in both directions.
        decided = await self._decided_pairs(project_id)

        written = 0
        for term, synonym, score, support in pairs:
            if (term, synonym) in decided or (synonym, term) in decided:
                continue
            self.session.add(
                LearnedTerm(
                    id=new_id("lrn"),
                    project_id=project_id,
                    term=term,
                    synonym=synonym,
                    score=score,
                    support=support,
                    source=VocabularySource.MINED,
                    status=VocabularyStatus.ACTIVE,
                    mined_at=now,
                )
            )
            written += 1
        await self.session.flush()
        return written

    async def _decided_pairs(self, project_id: str) -> set[tuple[str, str]]:
        result = await self.session.execute(
            select(LearnedTerm.term, LearnedTerm.synonym).where(
                LearnedTerm.project_id == project_id,
                or_(
                    LearnedTerm.status == VocabularyStatus.REJECTED,
                    LearnedTerm.source == VocabularySource.CURATED,
                ),
            )
        )
        return {(term, synonym) for term, synonym in result.all()}

    async def add_curated(
        self,
        *,
        project_id: str,
        term: str,
        synonym: str,
        decided_by: str | None = None,
        note: str | None = None,
    ) -> LearnedTerm:
        """Teach the project a pair directly. Replaces a mined or rejected row for it."""
        term, synonym = term.strip().lower(), synonym.strip().lower()
        if not term or not synonym:
            raise ConflictError("A glossary entry needs both a term and a synonym.")
        if term == synonym:
            raise ConflictError("A term cannot be a synonym of itself.")

        existing = await self.get_pair(project_id=project_id, term=term, synonym=synonym)
        if existing is not None:
            existing.source = VocabularySource.CURATED
            existing.status = VocabularyStatus.ACTIVE
            existing.score = 1.0
            existing.decided_by = decided_by
            existing.note = note
            existing.mined_at = utcnow()
            await self.session.flush()
            return existing

        entry = LearnedTerm(
            id=new_id("lrn"),
            project_id=project_id,
            term=term,
            synonym=synonym,
            score=1.0,
            support=0,
            source=VocabularySource.CURATED,
            status=VocabularyStatus.ACTIVE,
            decided_by=decided_by,
            note=note,
            mined_at=utcnow(),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def reject(
        self, *, project_id: str, term: str, synonym: str, decided_by: str | None = None
    ) -> LearnedTerm | None:
        """Throw a pair out, and remember that it was thrown out."""
        entry = await self.get_pair(project_id=project_id, term=term, synonym=synonym)
        if entry is None:
            return None
        entry.status = VocabularyStatus.REJECTED
        entry.decided_by = decided_by
        await self.session.flush()
        return entry

    async def restore(
        self, *, project_id: str, term: str, synonym: str
    ) -> LearnedTerm | None:
        """Undo a rejection. A mined row returns at the next mining run; curated at once."""
        entry = await self.get_pair(project_id=project_id, term=term, synonym=synonym)
        if entry is None:
            return None
        if entry.source == VocabularySource.MINED:
            # Its score is stale; drop it and let the miner decide again.
            await self.session.delete(entry)
            await self.session.flush()
            return None
        entry.status = VocabularyStatus.ACTIVE
        await self.session.flush()
        return entry

    async def get_pair(
        self, *, project_id: str, term: str, synonym: str
    ) -> LearnedTerm | None:
        result = await self.session.execute(
            select(LearnedTerm).where(
                LearnedTerm.project_id == project_id,
                LearnedTerm.term == term.strip().lower(),
                LearnedTerm.synonym == synonym.strip().lower(),
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        project_id: str,
        status: VocabularyStatus | None = VocabularyStatus.ACTIVE,
        source: VocabularySource | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[LearnedTerm], int]:
        filters = [LearnedTerm.project_id == project_id]
        if status:
            filters.append(LearnedTerm.status == status)
        if source:
            filters.append(LearnedTerm.source == source)

        total = await self.session.scalar(
            select(func.count()).select_from(LearnedTerm).where(*filters)
        )
        result = await self.session.execute(
            select(LearnedTerm)
            .where(*filters)
            # Curated first: what a person asserted outranks what the corpus suggested.
            .order_by(
                LearnedTerm.source.desc(),
                LearnedTerm.score.desc(),
                LearnedTerm.support.desc(),
                LearnedTerm.term,
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def expansion_table(
        self, *, project_id: str, limit: int = 400
    ) -> dict[str, tuple[str, ...]]:
        """The table retrieval uses, keyed by term, both directions."""
        result = await self.session.execute(
            select(LearnedTerm.term, LearnedTerm.synonym)
            .where(
                LearnedTerm.project_id == project_id,
                LearnedTerm.status == VocabularyStatus.ACTIVE,
            )
            .order_by(LearnedTerm.score.desc())
            .limit(limit)
        )
        table: dict[str, list[str]] = {}
        for term, synonym in result.all():
            table.setdefault(term, []).append(synonym)
            table.setdefault(synonym, []).append(term)
        return {term: tuple(dict.fromkeys(values)) for term, values in table.items()}

    async def last_mined_at(self, project_id: str) -> datetime | None:
        return await self.session.scalar(
            select(func.max(LearnedTerm.mined_at)).where(LearnedTerm.project_id == project_id)
        )
