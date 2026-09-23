"""When each encryption key came into use.

Rotation needs an answer to "how old is the active key?", and nothing else in the system
knows. The key itself is held in a KMS and injected as an environment variable; it has no
creation date the application can read, and inferring one from the rows it sealed is wrong
in both directions — a key used to rewrite old rows looks ancient, and a fresh key with no
secrets yet looks like it does not exist.

So the first time a process sees a key id, it records the date. That is the honest start of
its life in this deployment, and it is what the nightly check measures against.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class EncryptionKeyUse(Base):
    __tablename__ = "encryption_key_uses"

    # The key id from the sealed value's header, not the key. Nothing secret is stored.
    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
