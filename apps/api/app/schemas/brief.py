"""The customer brief (§26 5.2): decision-ready, with the evidence behind each part."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.changes import ChangeOut, WindowOut
from app.schemas.signals import RecommendationOut, SignalOut, Trajectory


class BriefCustomerOut(BaseModel):
    id: str
    external_id: str
    name: str | None = None
    email: str | None = None
    customer_since: datetime | None = None
    last_active_at: datetime | None = None


class BriefHealthOut(BaseModel):
    score: float = Field(ge=0, le=100)
    band: str
    churn_risk: float = Field(ge=0, le=1, description="The forecast's churn risk, 0–1.")
    trajectory: Trajectory
    explanation: str | None = None


class BriefPlanOut(BaseModel):
    name: str | None = Field(default=None, description="The current plan, lowercase; null when none is known.")
    statement: str | None = Field(default=None, description="The subscription memory the plan was read from.")
    changed_at: datetime | None = None
    direction: str | None = Field(
        default=None, description="How the latest statement changed it: upgraded, downgraded, cancelled, renewed, started or changed."
    )
    previous: str | None = Field(default=None, description="The plan before the latest change, lowercase.")


class BriefTrackOut(BaseModel):
    id: str | None = None
    track: str
    label: str
    state: str
    entered_at: datetime
    pinned: bool = False
    reasons: list[str] = Field(default_factory=list, description="Why they entered the state, when they did")
    reasons_now: list[str] | None = Field(
        default=None,
        description=(
            "Why they are in it now: the entering transition's clauses that still hold, or — when"
            " none does — what keeps them from the way out they are closest to. Null when a person"
            " set the state or it is the initial one."
        ),
    )
    holds: bool | None = Field(default=None, description="Whether the transition that brought them here would still fire")
    held_by: str | None = Field(default=None, description="When it would not: the way out they are closest to")
    moving_to: str | None = Field(
        default=None, description="Nothing keeps them here and a way out would fire: the state the next refresh moves them to"
    )


class BriefSituationOut(BaseModel):
    health: BriefHealthOut
    plan: BriefPlanOut
    lifecycle: list[BriefTrackOut] = Field(default_factory=list)
    why: list[str] = Field(
        default_factory=list,
        description="Why they are where they are: lifecycle reasons, what pulls health down, the risks seen.",
    )
    open_problems: int = Field(description="Every open problem — a count, so whole even when some are withheld.")
    goals: dict[str, int] = Field(default_factory=dict, description="Goals by status: open, progressing, stalled, achieved.")


class BriefCautionOut(BaseModel):
    text: str = Field(description='What not to do, in words: "Don\'t call them: the customer asked not to be called."')
    action: str = Field(description="The first action the caution covers.")
    actions: list[str] = Field(description="Every action refused for the same reason.")
    decision: Literal["deny", "require_approval"]
    summary: str = Field(description="The guardrail's own explanation.")
    rules: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class BriefIssueOut(BaseModel):
    id: str
    content: str
    first_seen_at: datetime | None = None
    age_days: int | None = None
    times_reported: int = 1
    freshness: str | None = Field(default=None, description="active, aging, stale, outdated or conflicted.")


class BriefGoalOut(BaseModel):
    id: str
    statement: str
    status: str
    progress: float | None = None
    last_signal_at: datetime | None = None


class BriefOptOutOut(BaseModel):
    kind: str
    words: str


class BriefStatementOut(BaseModel):
    id: str
    content: str
    freshness: str | None = None


class BriefPreferencesOut(BaseModel):
    channel: str | None = Field(default=None, description='The preferred channel as written: "email", "WhatsApp", "SMS".')
    opt_outs: list[BriefOptOutOut] = Field(default_factory=list)
    statements: list[BriefStatementOut] = Field(default_factory=list)
    channel_outdated: bool = Field(
        default=False, description="The stated channel is flagged possibly outdated by the channels they actually use."
    )
    observed_channel: str | None = None


class BriefIntentOut(BaseModel):
    id: str
    type: str = Field(description="The memory's type — churn language filed as a problem still counts.")
    content: str
    kinds: list[str]
    last_seen_at: datetime | None = None


class BriefChangesOut(BaseModel):
    window: WindowOut
    summary: str
    items: list[ChangeOut] = Field(default_factory=list, description="The most important changes, at most six.")
    total: int = 0
    withheld: int = 0


class BriefConversationOut(BaseModel):
    id: str
    agent: str | None = None
    summary: str
    closed_at: datetime | None = None
    turn_count: int | None = None


class BriefCareOut(BaseModel):
    topic: str
    detail: str
    evidence: list[str] = Field(default_factory=list)


class BriefEvidenceOut(BaseModel):
    memories: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    snapshots: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    drift: list[str] = Field(default_factory=list)


class BriefDriftOut(BaseModel):
    id: str
    kind: str
    kind_label: str
    memory_id: str
    stated: str
    observed: str | None = None
    summary: str
    detected_at: datetime


class BriefSetAsideOut(BaseModel):
    key: str
    action: str
    because: str = Field(description="The caution that forbids it.")


class CustomerBriefOut(BaseModel):
    customer: BriefCustomerOut
    headline: str = Field(description="The situation in a few sentences.")
    situation: BriefSituationOut
    cares_about: list[BriefCareOut] = Field(
        default_factory=list, description="Their goals, then the products and features they talk about most."
    )
    talking_points: list[str] = Field(default_factory=list, description="What to raise, most important first.")
    cautions: list[BriefCautionOut] = Field(default_factory=list, description="What not to do, and why.")
    open_issues: list[BriefIssueOut] = Field(default_factory=list)
    goals: list[BriefGoalOut] = Field(default_factory=list)
    preferences: BriefPreferencesOut
    intents: list[BriefIntentOut] = Field(default_factory=list)
    risks: list[SignalOut] = Field(default_factory=list)
    opportunities: list[SignalOut] = Field(default_factory=list)
    recent_changes: BriefChangesOut
    last_conversation: BriefConversationOut | None = None
    drift: list[BriefDriftOut] = Field(
        default_factory=list, description="What evidence says may be out of date — to ask about, not to act on."
    )
    next_step: RecommendationOut | None = Field(
        default=None, description="The most urgent recommendation none of the cautions forbids."
    )
    set_aside: list[BriefSetAsideOut] = Field(
        default_factory=list, description="More urgent recommendations a caution forbids, and which caution."
    )
    evidence: list[str] = Field(default_factory=list, description="Every memory and goal id the brief rests on.")
    evidence_refs: BriefEvidenceOut = Field(
        default_factory=BriefEvidenceOut, description="The evidence by kind: memories, goals, snapshots, states, drift flags."
    )
    withheld: int = Field(default=0, description="Memories this caller may not see.")
    withheld_facts: list[str] = Field(default_factory=list, description="Facts shown without what came only from withheld memories.")
    generated_at: datetime
    markdown: str = Field(description="The brief as a page, for a person or a model's context.")
