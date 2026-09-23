"""Repository base class.

Repositories own all SQL. Services never build queries themselves, which keeps the
tenant-scoping rule ("every query filters on project_id") enforceable in one place.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def flush(self) -> None:
        await self.session.flush()
