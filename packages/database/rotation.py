"""How overdue the secrets encryption key is, and whether the last rotation finished.

Two questions, and the second is the one that bites. A rotation is two steps — set the new
key, then run ``scripts/encrypt_secrets.py --apply`` — and a deployment that does the first
and forgets the second looks fine: everything still decrypts, because the old key is still
in ``PREVIOUS_ENCRYPTION_KEYS``. It stays fine right up until somebody retires the old key,
at which point the secrets sealed under it are gone. So the check reports stale rows as
well as age, and treats a half-finished rotation as the more urgent of the two.

The reads are raw SQL for the same reason the rotation script uses it: the column type
decrypts on the way out, so a typed read would hide which key each row is sealed under.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.crypto import EncryptionKey, needs_rewrite
from common.time import utcnow
from database.models import EncryptionKeyUse


@dataclass(slots=True, frozen=True)
class RotationStatus:
    """What the nightly check found."""

    key_id: str
    first_seen_at: datetime
    age_days: int
    max_age_days: int
    stale_secrets: int

    @property
    def overdue(self) -> bool:
        return self.max_age_days > 0 and self.age_days > self.max_age_days

    @property
    def unfinished(self) -> bool:
        """A rotation was started and the rewrite never ran."""
        return self.stale_secrets > 0

    @property
    def needs_attention(self) -> bool:
        return self.overdue or self.unfinished

    def as_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "age_days": self.age_days,
            "max_age_days": self.max_age_days,
            "stale_secrets": self.stale_secrets,
            "overdue": self.overdue,
            "unfinished": self.unfinished,
        }


async def record_key_use(session: AsyncSession, key_id: str) -> EncryptionKeyUse:
    """Note that this key is in use, remembering when it first was."""
    now = utcnow()
    record = await session.get(EncryptionKeyUse, key_id)
    if record is None:
        record = EncryptionKeyUse(key_id=key_id, first_seen_at=now, last_seen_at=now)
        session.add(record)
    else:
        record.last_seen_at = now
    await session.flush()
    return record


async def count_stale_secrets(session: AsyncSession, active_id: str) -> int:
    """Secrets still plaintext, or still sealed under a retired key."""
    stale = 0

    rows = (await session.execute(text("SELECT secret FROM webhook_endpoints"))).all()
    stale += sum(1 for (stored,) in rows if needs_rewrite(stored, active_id))

    settings_rows = (await session.execute(text("SELECT settings FROM projects"))).all()
    for (stored_settings,) in settings_rows:
        integrations = (stored_settings or {}).get("integrations") or {}
        if not isinstance(integrations, dict):
            continue
        for config in integrations.values():
            if isinstance(config, dict) and needs_rewrite(config.get("signing_secret"), active_id):
                stale += 1

    return stale


async def rotation_status(
    session: AsyncSession, *, active: EncryptionKey, max_age_days: int
) -> RotationStatus:
    """Age of the active key and how much of the last rotation is outstanding."""
    record = await record_key_use(session, active.id)
    age_days = max(0, (utcnow() - record.first_seen_at).days)
    return RotationStatus(
        key_id=active.id,
        first_seen_at=record.first_seen_at,
        age_days=age_days,
        max_age_days=max_age_days,
        stale_secrets=await count_stale_secrets(session, active.id),
    )


async def known_keys(session: AsyncSession) -> list[EncryptionKeyUse]:
    """Every key this deployment has used, oldest first."""
    result = await session.execute(
        select(EncryptionKeyUse).order_by(EncryptionKeyUse.first_seen_at)
    )
    return list(result.scalars())
