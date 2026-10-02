"""Memory contracts (§26 7.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContractIn(BaseModel):
    """A contract — as an object, or as YAML text in ``yaml``."""

    event_type: str | None = Field(default=None, max_length=128, description="Or 'event', as in YAML.")
    mode: str | None = Field(default=None, description="warn (default), enforce or off")
    description: str | None = Field(default=None, max_length=500)
    required: list[str] | None = Field(default=None, description="Field paths that must be present")
    fields: dict[str, Any] | None = Field(default=None, description="path → {type, enum, minimum, maximum, max_length, pattern}")
    text_field: str | None = Field(default=None, description="The path that carries the human-written text")
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_extra: bool | None = Field(default=None, description="False flags fields the contract does not name")
    yaml: str | None = Field(default=None, max_length=20_000, description="The whole contract as YAML instead")

    def raw(self) -> dict[str, Any] | None:
        if self.yaml is not None:
            return None
        return self.model_dump(exclude_none=True, exclude={"yaml"})


class ContractRejectionsOut(BaseModel):
    count: int = 0
    last_at: datetime | None = None
    recent: list[dict[str, Any]] = Field(default_factory=list)


class ContractViolationOut(BaseModel):
    path: str
    rule: str
    expected: str | None = None
    received: str | None = None
    events: int
    last_seen_at: datetime | None = None
    latest_event_id: str | None = None


class ContractReportOut(BaseModel):
    events: int = Field(description="Events of this type received in the window")
    checked: int = Field(description="Of those, checked against a contract")
    violating: int = Field(description="Of those, breaking it")
    violations: list[ContractViolationOut] = Field(default_factory=list)
    recent: list[dict[str, Any]] = Field(default_factory=list, description="The latest violating events")


class ContractOut(BaseModel):
    id: str
    event_type: str
    mode: str
    version: int
    definition: dict[str, Any]
    rejected: ContractRejectionsOut
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None
    events: int | None = Field(default=None, description="Events of this type in the window (lists)")
    violating: int | None = Field(default=None, description="Of those, violating (lists)")
    report: ContractReportOut | None = None


class ContractTestIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict, description="The payload to check")
    contract: dict[str, Any] | None = Field(default=None, description="A proposed contract to check against instead")
    yaml: str | None = Field(default=None, max_length=20_000)


class ContractTestOut(BaseModel):
    event_type: str
    version: int | None = Field(default=None, description="The saved contract's version; null for a proposed one")
    mode: str
    valid: bool
    would_refuse: bool
    violations: list[dict[str, Any]]
    text: str | None = Field(default=None, description="What the text field holds, redacted")
    definition: dict[str, Any] = Field(
        default_factory=dict, description="The contract the payload was checked against, as the server reads it"
    )


class ContractCoverageOut(BaseModel):
    event_type: str
    events: int
    violating: int
    last_seen_at: datetime | None = None
    mode: str | None = Field(default=None, description="The contract's mode; null when none covers the type")
    version: int | None = None
