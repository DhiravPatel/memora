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


class ContractViolationError(MemoryAPIError):
    """An enforcing memory contract refused the event (§26 7.1); nothing was stored.

    ``violations`` says every way the payload broke the contract — what was expected and
    what arrived — so the sending code can be fixed, or the payload logged and dropped.
    """

    @property
    def event_type(self) -> str | None:
        return self.details.get("event_type")

    @property
    def contract_version(self) -> int | None:
        return self.details.get("contract_version")

    @property
    def violations(self) -> list[Any]:
        from ai_memory.models import ContractViolation

        return [ContractViolation.from_api(item) for item in self.details.get("violations") or []]


class MemoryConfigError(MemoryError):
    pass


class MemoryTimeoutError(MemoryError):
    pass


class ActionDenied(MemoryError):
    """A guardrail refused the action. ``check`` holds every reason and its evidence."""

    def __init__(self, check: Any) -> None:
        super().__init__(getattr(check, "summary", "") or "The action was denied.")
        self.check = check


class ApprovalRequired(MemoryError):
    """The action needs a person first. ``approval`` is the request that was filed."""

    def __init__(self, check: Any) -> None:
        super().__init__(getattr(check, "summary", "") or "The action needs approval.")
        self.check = check
        self.approval = getattr(check, "approval", None)
