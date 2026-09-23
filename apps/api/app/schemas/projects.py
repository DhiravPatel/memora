"""Project and API key schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    settings: dict[str, Any] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: dict[str, Any] | None = None


class ProjectOut(BaseModel):
    id: str
    organization_id: str
    name: str
    api_key_prefix: str
    settings: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProjectWithKey(ProjectOut):
    """Returned once, at creation or rotation: the raw key is never stored."""

    api_key: str


class SettingFieldOut(BaseModel):
    """One tunable, described well enough for a form to render itself."""

    key: str
    label: str
    group: str
    kind: str
    default: Any = None
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str | None = None
    keys: list[str] = Field(default_factory=list)


class SettingsGroupOut(BaseModel):
    key: str
    label: str


class ProjectSettingsOut(BaseModel):
    project_id: str
    values: dict[str, Any]
    defaults: dict[str, Any]
    groups: list[SettingsGroupOut]
    fields: list[SettingFieldOut]


class ProjectSettingsUpdate(BaseModel):
    settings: dict[str, Any] = Field(description="Partial patch; unknown keys pass through")
