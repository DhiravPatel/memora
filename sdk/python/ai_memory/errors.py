"""Errors raised by the SDK."""

from __future__ import annotations

from typing import Any


class MemoryError(Exception):
    """Base class for every SDK error."""


class MemoryAPIError(MemoryError):
    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str = "error",
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}
        self.request_id = request_id

    @property
    def is_retryable(self) -> bool:
        return self.status == 429 or self.status >= 500

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        suffix = f" (request {self.request_id})" if self.request_id else ""
        return f"[{self.status} {self.code}] {super().__str__()}{suffix}"


class MemoryConfigError(MemoryError):
    pass


class MemoryTimeoutError(MemoryError):
    pass
