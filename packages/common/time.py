"""Time helpers. All timestamps in the system are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalise a datetime to UTC, assuming naive datetimes are already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def days_between(earlier: datetime, later: datetime) -> float:
    return (ensure_utc(later) - ensure_utc(earlier)).total_seconds() / 86400.0


def days_ago(days: float) -> datetime:
    return utcnow() - timedelta(days=days)
