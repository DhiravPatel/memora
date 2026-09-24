"""Evaluation sets, their cases, and the runs against them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from common.ids import new_id
from common.time import utcnow
from database.models import EvalCase, EvalRun, EvalSet
from database.repositories.base import BaseRepository


class EvalRepository(BaseRepository):
    # ------------------------------------------------------------------ sets

    async def create_set(
        self, *, project_id: str, name: str, description: str | None, created_by: str | None
    ) -> EvalSet:
        row = EvalSet(
            id=new_id("evs"),
            project_id=project_id,
            name=name,
            description=description,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_set(self, set_id: str, project_id: str) -> EvalSet | None:
        result = await self.session.execute(
            select(EvalSet).where(EvalSet.id == set_id, EvalSet.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_set_by_name(self, project_id: str, name: str) -> EvalSet | None:
        result = await self.session.execute(
            select(EvalSet).where(EvalSet.project_id == project_id, func.lower(EvalSet.name) == name.lower())
        )
        return result.scalar_one_or_none()

    async def list_sets(self, project_id: str) -> list[EvalSet]:
        result = await self.session.execute(
            select(EvalSet).where(EvalSet.project_id == project_id).order_by(EvalSet.created_at.desc())
        )
        return list(result.scalars())

    async def delete_set(self, row: EvalSet) -> None:
        await self.session.execute(delete(EvalSet).where(EvalSet.id == row.id))

    async def case_counts(self, set_ids: list[str]) -> dict[str, int]:
        if not set_ids:
            return {}
        result = await self.session.execute(
            select(EvalCase.set_id, func.count()).where(EvalCase.set_id.in_(set_ids)).group_by(EvalCase.set_id)
        )
        return {set_id: int(count) for set_id, count in result}

    # ----------------------------------------------------------------- cases

    async def add_case(
        self,
        *,
        set_id: str,
        project_id: str,
        customer_id: str,
        question: str,
        expected_memory_ids: list[str],
        expected_phrases: list[str],
        notes: str | None,
        source: str = "manual",
    ) -> EvalCase:
        row = EvalCase(
            id=new_id("evc"),
            set_id=set_id,
            project_id=project_id,
            customer_id=customer_id,
            question=question,
            expected_memory_ids=expected_memory_ids,
            expected_phrases=expected_phrases,
            notes=notes,
            source=source,
            created_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def cases(self, set_id: str, project_id: str) -> list[EvalCase]:
        result = await self.session.execute(
            select(EvalCase)
            .where(EvalCase.set_id == set_id, EvalCase.project_id == project_id)
            .order_by(EvalCase.created_at)
        )
        return list(result.scalars())

    async def delete_case(self, *, case_id: str, set_id: str, project_id: str) -> bool:
        result = await self.session.execute(
            delete(EvalCase).where(
                EvalCase.id == case_id, EvalCase.set_id == set_id, EvalCase.project_id == project_id
            )
        )
        return bool(result.rowcount)

    # ------------------------------------------------------------------ runs

    async def create_run(
        self,
        *,
        set_id: str,
        project_id: str,
        label: str | None,
        cleared: bool,
        k: int,
        settings: dict[str, Any],
        baseline_run_id: str | None,
        created_by: str | None,
    ) -> EvalRun:
        row = EvalRun(
            id=new_id("evr"),
            set_id=set_id,
            project_id=project_id,
            status="queued",
            label=label,
            cleared=cleared,
            k=k,
            settings=settings,
            baseline_run_id=baseline_run_id,
            created_by=created_by,
            created_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_run(self, run_id: str, project_id: str | None = None) -> EvalRun | None:
        conditions = [EvalRun.id == run_id]
        if project_id is not None:
            conditions.append(EvalRun.project_id == project_id)
        result = await self.session.execute(select(EvalRun).where(*conditions))
        return result.scalar_one_or_none()

    async def runs(self, set_id: str, project_id: str, limit: int = 20) -> list[EvalRun]:
        result = await self.session.execute(
            select(EvalRun)
            .where(EvalRun.set_id == set_id, EvalRun.project_id == project_id)
            .order_by(EvalRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def latest_succeeded(self, set_id: str, *, before: datetime | None = None) -> EvalRun | None:
        conditions = [EvalRun.set_id == set_id, EvalRun.status == "succeeded"]
        if before is not None:
            conditions.append(EvalRun.created_at < before)
        result = await self.session.execute(
            select(EvalRun).where(*conditions).order_by(EvalRun.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_by_set(self, set_ids: list[str]) -> dict[str, EvalRun]:
        latest: dict[str, EvalRun] = {}
        for set_id in set_ids:
            run = await self.latest_succeeded(set_id)
            if run is not None:
                latest[set_id] = run
        return latest

    async def update_run(self, run: EvalRun, **values: Any) -> None:
        await self.session.execute(update(EvalRun).where(EvalRun.id == run.id).values(**values))
        for key, value in values.items():
            setattr(run, key, value)
