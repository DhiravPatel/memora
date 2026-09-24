"""What the current reader may see, beyond clearance: the memory types of their agent profile.

Clearance (§17c) is handed to a repository explicitly. An agent profile's type restriction
(§26 3.1) travels with the request instead: ``get_api_project`` sets it from the key's
bound profile before any route runs, and every *reader-bound* repository — one built with
an explicit ``cleared=`` — reads it when constructed. A service that already honours
clearance therefore honours the profile too, with no second argument for a new route to
forget.

System repositories (``MemoryRepository(session)``) ignore it on purpose. Health, facts
and guardrail decisions are computed over everything and *shown* through redaction — the
rule restricted memories already follow — because two readers must not get two health
scores for one customer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_

from common.enums import Sensitivity


@dataclass(frozen=True, slots=True)
class MemoryAccess:
    """Who is reading, reduced to what reads and run records need."""

    profile_id: str | None = None
    profile: str | None = None
    # Memory types the reader may see. ``None`` means every type; a profile's empty list
    # means the same, so this is never an empty set.
    readable_types: frozenset[str] | None = None
    # Recorded on agent runs and checks (§26 3.4), so "which agent asked?" has an answer.
    api_key_id: str | None = None
    # The profile's name when one is bound — which the caller cannot override — otherwise
    # whatever the caller labelled itself with, if anything.
    agent: str | None = None

    @property
    def restricts_types(self) -> bool:
        return self.readable_types is not None

    def can_read(self, memory_type: Any) -> bool:
        return self.readable_types is None or str(memory_type) in self.readable_types


UNRESTRICTED = MemoryAccess()

_current: ContextVar[MemoryAccess] = ContextVar("memory_access", default=UNRESTRICTED)


def current_access() -> MemoryAccess:
    return _current.get()


def set_access(access: MemoryAccess) -> Token[MemoryAccess]:
    return _current.set(access)


def reset_access(token: Token[MemoryAccess]) -> None:
    _current.reset(token)


@contextmanager
def access_scope(access: MemoryAccess) -> Iterator[MemoryAccess]:
    """Run a block as a given reader — the worker replaying an agent's run, and tests."""
    token = _current.set(access)
    try:
        yield access
    finally:
        _current.reset(token)


def access_for_types(types: list[str] | tuple[str, ...] | None, **identity: Any) -> MemoryAccess:
    """A profile's stored list as an access: empty or missing means every type."""
    cleaned = frozenset(str(item).strip().lower() for item in types or () if str(item).strip())
    return MemoryAccess(readable_types=cleaned or None, **identity)


# ------------------------------------------------------------------------ SQL


def visible_conditions(*, cleared: bool, readable_types: frozenset[str] | None) -> list[Any]:
    """WHERE clauses keeping only the memories this reader may see."""
    from database.models import Memory

    conditions: list[Any] = []
    if not cleared:
        conditions.append(Memory.sensitivity != Sensitivity.RESTRICTED)
    if readable_types is not None:
        conditions.append(Memory.type.in_(sorted(readable_types)))
    return conditions


def hidden_condition(*, cleared: bool, readable_types: frozenset[str] | None) -> Any | None:
    """A clause matching exactly the memories this reader may *not* see, or ``None``."""
    from database.models import Memory

    parts: list[Any] = []
    if not cleared:
        parts.append(Memory.sensitivity == Sensitivity.RESTRICTED)
    if readable_types is not None:
        parts.append(Memory.type.notin_(sorted(readable_types)))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else or_(*parts)
