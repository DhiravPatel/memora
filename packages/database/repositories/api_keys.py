"""API key persistence.

Lookup is by HMAC digest only — the raw key never touches the database, and the digest is
indexed so authentication stays a single indexed read on the hot path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from common.ids import new_id
from common.time import utcnow
from database.models import ApiKey
from database.repositories.base import BaseRepository


class ApiKeyRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        scopes: Sequence[str],
        created_by: str | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApiKey:
        key = ApiKey(
            id=new_id("key"),
            project_id=project_id,
            name=name.strip(),
            key_hash=key_hash,
            key_prefix=key_prefix,
            scopes=list(scopes),
            created_by=created_by,
            expires_at=expires_at,
            meta=metadata or {},
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self.session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def get(self, key_id: str, project_id: str) -> ApiKey | None:
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def list(self, project_id: str, *, include_revoked: bool = False) -> list[ApiKey]:
        conditions = [ApiKey.project_id == project_id]
        if not include_revoked:
            conditions.append(ApiKey.revoked_at.is_(None))
        result = await self.session.execute(
            select(ApiKey).where(*conditions).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars())

    async def revoke(self, key: ApiKey) -> ApiKey:
        key.revoked_at = utcnow()
        await self.session.flush()
        return key

    async def update_scopes(self, key: ApiKey, scopes: Sequence[str]) -> ApiKey:
        key.scopes = list(scopes)
        await self.session.flush()
        return key

    async def touch(self, key_id: str, *, ip_address: str | None = None) -> None:
        """Record usage without loading the row: this runs on every authenticated request."""
        await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=utcnow(), last_used_ip=ip_address, use_count=ApiKey.use_count + 1)
        )

    async def count_active(self, project_id: str) -> int:
        result = await self.session.execute(
            select(ApiKey.id).where(ApiKey.project_id == project_id, ApiKey.revoked_at.is_(None))
        )
        return len(result.all())
