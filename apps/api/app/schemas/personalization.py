"""What a product should do differently for a customer (§26 6.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RuleResultOut(BaseModel):
    value: str | None = None
    because: list[str] = Field(default_factory=list)
    rule: str | None = Field(default=None, description="The condition that decided it; null for the fallback.")


class ChannelOut(BaseModel):
    value: str | None = None
    outdated: bool | None = Field(default=None, description="Evidence since says otherwise (§26 5.5).")
    observed: str | None = Field(default=None, description="The channel the evidence points to, when outdated.")
    opt_outs: list[str] = Field(default_factory=list)


class GoalOut(BaseModel):
    id: str
    key: str
    label: str
    status: str
    progress: float = 0.0
    memory_id: str | None = None


class FrictionOut(BaseModel):
    key: str = Field(description="The product, integration or feature named, else the area: shopify, billing, login…")
    label: str
    area: str | None = None
    mode: str = Field(description="How it fails: broken, slow or other.")
    problems: int
    reports: int
    since: datetime
    last_reported_at: datetime
    memory_ids: list[str] = Field(default_factory=list)


class FeatureOut(BaseModel):
    key: str
    name: str
    kind: str = Field(description="feature or integration")
    uses: int
    recent_uses: int
    first_used_at: datetime
    last_used_at: datetime
    relied_on: bool


class HintOut(BaseModel):
    value: bool
    known: bool = Field(description="False when a fact the hint reads is unknown — the hint is then off.")
    because: list[str] = Field(default_factory=list)
    rule: str
    description: str = ""
    custom: bool = False


class PersonalizationDetailsOut(BaseModel):
    experience: RuleResultOut
    mood: RuleResultOut
    preferred_channel: ChannelOut
    current_goal: GoalOut | None = None
    known_frictions: list[FrictionOut] = Field(default_factory=list)
    features_used: list[FeatureOut] = Field(default_factory=list)
    stage: dict[str, Any] = Field(default_factory=dict, description="Every lifecycle track's state.")
    health: dict[str, Any] = Field(default_factory=dict)
    ui: dict[str, HintOut] = Field(default_factory=dict)


class PersonalizationOut(BaseModel):
    customer_id: str
    experience: str | None = Field(default=None, description="new, beginner, intermediate, advanced — or the project's own")
    mood: str | None = Field(default=None, description="frustrated, happy, neutral — or the project's own")
    preferred_channel: str | None = None
    opt_outs: list[str] = Field(default_factory=list)
    current_goal: str | None = Field(default=None, description="The goal's key, e.g. launch_automation")
    known_frictions: list[str] = Field(default_factory=list, description="Keys, e.g. shopify, billing")
    features_used: list[str] = Field(default_factory=list)
    relied_on_features: list[str] = Field(default_factory=list)
    stage: str | None = Field(default=None, description="The primary lifecycle state")
    plan: str | None = None
    health: str | None = Field(default=None, description="The health band")
    ui: dict[str, bool] = Field(default_factory=dict, description="UI hints: show_onboarding, suppress_upsell, …")
    evidence: list[str] = Field(default_factory=list, description="The memory and goal ids behind it")
    details: PersonalizationDetailsOut | None = Field(
        default=None, description="Why each value is what it is — omitted with ?details=false"
    )
    computed_at: datetime
    changed_at: datetime
    version: str = Field(description="Changes only when what the product sees changes — also the ETag")


class PersonalizationBatchIn(BaseModel):
    customer_ids: list[str] = Field(min_length=1, max_length=50)
    details: bool = False


class PersonalizationBatchItemOut(BaseModel):
    customer_id: str
    personalization: PersonalizationOut | None = None
    error: str | None = Field(default=None, description="not_found when no such customer")


class PersonalizationBatchOut(BaseModel):
    data: list[PersonalizationBatchItemOut]


class PersonalizationRulesOut(BaseModel):
    experience: list[dict[str, Any]]
    mood: list[dict[str, Any]]
    hints: list[dict[str, Any]]
    relied_on_uses: int
    relied_on_days: int
    builtin_hints: list[str]
    defaults: dict[str, Any] = Field(default_factory=dict, description="The built-in rules, for comparison")


class PersonalizationPreviewIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    rules: dict[str, Any] | None = Field(default=None, description="Proposed rules, as the settings take them")


class PersonalizationSummaryOut(BaseModel):
    customers: int
    hints: dict[str, int] = Field(default_factory=dict, description="Customers with each hint on")
    experience: dict[str, int] = Field(default_factory=dict)
    mood: dict[str, int] = Field(default_factory=dict)
    frictions: list[dict[str, Any]] = Field(default_factory=list)
    relied_on_features: list[dict[str, Any]] = Field(default_factory=list)
    oldest_computed_at: datetime | None = None
