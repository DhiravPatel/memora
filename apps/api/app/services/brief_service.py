"""The customer brief, gathered (§26 5.2).

Every part comes from the service that owns it — the rule Customer 360 follows (§12b) —
and the expensive parts are computed once: health and the forecast feed the fact document,
the fact document feeds the guardrail previews and "what changed", and only then is each
part shaped for the reader. The judgement — headline, talking points, cautions, the page —
is :mod:`memory_engine.brief`, which only ever sees what the reader may.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.changes_service import ChangesService
from app.services.customer360_service import Customer360Service
from app.services.customer_state_service import machines_for, track_label
from app.services.drift_service import DriftService
from app.services.facts_service import FactsService
from app.services.freshness_service import FreshnessService
from app.services.guardrail_service import GuardrailService
from app.services.health_service import HealthService
from app.services.reader import Reader
from app.services.signal_service import SignalService
from app.services.state_views import sanitizing, state_out
from common.time import ensure_utc, utcnow
from database.models import AgentProfile, Customer, Project
from database.repositories import CustomerStateRepository, EventRepository, MemoryRepository
from memory_engine import MemoryEngine
from memory_engine.analytics.health import explain
from memory_engine.brief import (
    CAUTION_ACTIONS,
    HEADLINE_INTENTS,
    BriefParts,
    channel_name,
    compose,
    markdown,
    opt_out_words,
)
from memory_engine.facts import CustomerFacts
from memory_engine.policy import WITHHELD
from nlp.intents import intent_kinds

OPEN_ISSUES = 6
GOALS = 6
PREFERENCES = 6
CHANGES = 6
SIGNALS = 4
DRIFT = 10
# An achieved goal is worth raising for a month; after that it is history.
ACHIEVED_RECENTLY = timedelta(days=30)
LIVE_GOALS = ("open", "progressing", "stalled")
SECTIONS = frozenset({"subscription", "active_problems", "preferences", "goals", "recent_conversations"})


class BriefService:
    def __init__(self, session: AsyncSession, engine: MemoryEngine, *, cleared: bool) -> None:
        self.session = session
        self.engine = engine
        self.cleared = cleared
        self.reader = Reader(session, cleared=cleared)

    async def build(
        self,
        *,
        project: Project,
        customer: Customer,
        since: str | None = "last_session",
        agent: str | None = None,
        profile: AgentProfile | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        # Decisions are made over everything, and each is computed once…
        health = (await HealthService(self.session).for_customer(project=project, customer=customer)).health
        actions, report = await SignalService(self.session).recommendations(project=project, customer=customer)
        facts_service = FactsService(self.session)
        facts = await facts_service.for_customer(project=project, customer=customer, health=health, report=report)
        visible = await facts_service.visible(project=project, facts=facts, cleared=self.cleared)
        cautions = await GuardrailService(self.session, cleared=self.cleared).preview(
            project=project, actions=CAUTION_ACTIONS, facts=facts, visible=visible, profile=profile
        )
        # …and shown as this reader may see them.
        shown_actions = await self.reader.recommendations(project.id, actions)
        shown_report = await self.reader.report(project.id, report)
        view = await Customer360Service(self.session, self.engine, cleared=self.cleared).build(
            project=project, customer=customer, include=SECTIONS
        )

        changes_service = ChangesService(self.session, cleared=self.cleared)
        window = await changes_service.window(project=project, customer=customer, since=since, agent=agent)
        changes = await changes_service.changes(
            project=project,
            customer=customer,
            window=window,
            order="importance",
            limit=CHANGES,
            live=(dict(visible.values), {signal.key: signal.label for signal in report.signals}),
        )

        sections = view.sections
        # A customer's history begins at their first event, which an import can date long
        # before the record was created.
        first_event = await EventRepository(self.session).first_occurred_at(project_id=project.id, customer_id=customer.id)
        since_when = min((moment for moment in (customer.created_at, first_event) if moment), key=ensure_utc, default=None)
        lifecycle = await self._lifecycle(project, customer)
        intents = await self._intents(project, visible)
        goals = _live_goals(sections.get("goals") or [], now)
        problems = (sections.get("active_problems") or [])[:OPEN_ISSUES]
        preferences = (sections.get("preferences") or [])[:PREFERENCES]
        plan, statement, changed_at = _plan(visible, sections.get("subscription"))
        opt_outs = [str(kind) for kind in visible.get("preferences.opt_outs") or []]
        conversation = _last_conversation(sections.get("recent_conversations") or [])
        change_items = [change.model_dump() for change in changes.changes]
        # What evidence says may be out of date, and how current each quoted memory is (§26 5.5).
        flags, _, _ = await DriftService(self.session, cleared=self.cleared).list(
            project=project, customer=customer, status="open", limit=DRIFT
        )
        quoted_rows = await MemoryRepository(self.session, cleared=self.cleared).get_many(
            [str(item["id"]) for item in (*problems, *preferences) if item.get("id")], project.id
        )
        states = {
            ident: item.state
            for ident, item in (
                await FreshnessService(self.session, cleared=self.cleared).annotate(project=project, memories=quoted_rows)
            ).items()
        }

        parts = BriefParts(
            name=customer.name or customer.external_id,
            now=now,
            customer_since=since_when,
            last_active=customer.last_event_at,
            health={"score": health.score, "band": str(health.band)},
            trajectory=str(shown_report.trajectory),
            churn_risk=round(shown_report.churn_risk, 3),
            plan=plan,
            plan_statement=statement,
            plan_changed_at=changed_at,
            plan_direction=visible.get("subscription.direction"),
            previous_plan=visible.get("subscription.previous_plan"),
            plan_changed_days=_whole(visible.get("subscription.changed_days_ago")),
            lifecycle=lifecycle,
            open_problems=int(visible.get("problems.open_count") or 0),
            problems=problems,
            goals=goals,
            preferences=preferences,
            channel=channel_name(visible.get("preferences.channel")),
            opt_outs=opt_outs,
            intents=intents,
            risks=[signal.as_dict() for signal in shown_report.risks][:SIGNALS],
            opportunities=[signal.as_dict() for signal in shown_report.opportunities][:SIGNALS],
            recommendations=[action.as_dict() for action in shown_actions],
            conversation=conversation,
            changes={"summary": changes.summary, "label": changes.window.label, "items": change_items},
            cautions=cautions,
            drift=[
                {
                    "id": flag["id"],
                    "kind": flag["kind"],
                    "stated": flag["stated"],
                    "observed": flag["observed"],
                    "summary": flag["summary"],
                    "counts": flag["counts"],
                    "memory_id": flag["memory"]["id"],
                }
                for flag in flags
            ],
        )
        judged = compose(parts)
        brief: dict[str, Any] = {
            "customer": {
                "id": customer.id,
                "external_id": customer.external_id,
                "name": customer.name,
                "email": customer.email,
                "customer_since": since_when,
                "last_active_at": customer.last_event_at,
            },
            "headline": judged["headline"],
            "situation": {
                "health": {
                    "score": round(float(health.score), 1),
                    "band": str(health.band),
                    "churn_risk": round(shown_report.churn_risk, 3),
                    "trajectory": str(shown_report.trajectory),
                    "explanation": explain(health),
                },
                "plan": {
                    "name": plan,
                    "statement": statement,
                    "changed_at": changed_at,
                    "direction": visible.get("subscription.direction"),
                    "previous": visible.get("subscription.previous_plan"),
                },
                "lifecycle": lifecycle,
                "open_problems": parts.open_problems,
                "goals": {
                    status: int(visible.get(f"goals.{status}_count") or 0)
                    for status in ("open", "progressing", "stalled", "achieved")
                },
            },
            "talking_points": judged["talking_points"],
            "cautions": judged["cautions"],
            "open_issues": [
                {
                    "id": item["id"],
                    "content": item["content"],
                    "first_seen_at": item.get("first_seen_at"),
                    "age_days": _days(item.get("first_seen_at"), now),
                    "times_reported": int(item.get("evidence_count") or 1),
                    "freshness": states.get(str(item["id"])),
                }
                for item in problems
            ],
            "goals": [
                {
                    "id": goal["id"],
                    "statement": goal["statement"],
                    "status": goal["status"],
                    "progress": goal.get("progress"),
                    "last_signal_at": goal.get("last_signal_at"),
                }
                for goal in goals
            ],
            "preferences": {
                "channel": parts.channel,
                "opt_outs": opt_out_words(opt_outs),
                "statements": [
                    {"id": item["id"], "content": item["content"], "freshness": states.get(str(item["id"]))}
                    for item in preferences
                ],
                "channel_outdated": bool(visible.get("preferences.channel_outdated")),
                "observed_channel": channel_name(visible.get("preferences.observed_channel")),
            },
            "drift": [
                {
                    "id": flag["id"],
                    "kind": flag["kind"],
                    "kind_label": flag["kind_label"],
                    "memory_id": flag["memory"]["id"],
                    "stated": flag["stated"],
                    "observed": flag["observed"],
                    "summary": flag["summary"],
                    "detected_at": flag["detected_at"],
                }
                for flag in flags
            ],
            "intents": intents,
            "risks": parts.risks,
            "opportunities": parts.opportunities,
            "recent_changes": {
                "window": changes.window.model_dump(),
                "summary": changes.summary,
                "items": change_items,
                "total": changes.total,
                "withheld": changes.withheld,
            },
            "last_conversation": conversation,
            "next_step": judged["next_step"],
            "set_aside": judged["set_aside"],
            "evidence": _evidence(parts, judged["next_step"], judged["cautions"]),
            "withheld": view.withheld,
            "withheld_facts": list(visible.withheld_facts),
            "generated_at": now,
        }
        brief["markdown"] = markdown(brief)
        return brief

    async def _lifecycle(self, project: Project, customer: Customer) -> list[dict[str, Any]]:
        """Where the customer stands on every track, with the reasons in words."""
        machines = machines_for(project)
        if not machines:
            return []
        currents = await CustomerStateRepository(self.session).currents(project_id=project.id, customer_id=customer.id)
        sanitize = await sanitizing(self.session, project, customer, self.cleared)
        found = []
        for name in machines:
            row = currents.get(name)
            if row is None:
                continue
            shaped = state_out(row, sanitize=sanitize)
            found.append(
                {
                    "track": name,
                    "label": track_label(project, name),
                    "state": shaped.state,
                    "entered_at": shaped.entered_at,
                    "pinned": shaped.pinned,
                    "reasons": shaped.reasons,
                }
            )
        return found

    async def _intents(self, project: Project, visible: CustomerFacts) -> list[dict[str, Any]]:
        """The newest statement behind each intent worth raising.

        Read off the fact document rather than intent memories alone: "we will cancel if
        this is not fixed" is often filed as a problem, and a brief that missed it because
        of the extractor's filing would bury the one sentence that matters most.
        """
        index = visible.evidence_by_value.get("intents.kinds") or {}
        kinds = [kind for kind in HEADLINE_INTENTS if kind in (visible.get("intents.kinds") or []) and index.get(kind)]
        ids = list(dict.fromkeys(ident for kind in kinds for ident in index[kind]))
        if not ids:
            return []
        rows = await MemoryRepository(self.session, cleared=self.cleared).get_many(ids, project.id)
        by_id = {row.id: row for row in rows}
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for kind in kinds:
            candidates = [by_id[ident] for ident in index[kind] if ident in by_id]
            if not candidates:
                continue
            newest = max(candidates, key=lambda row: ensure_utc(row.last_seen_at))
            if newest.id in seen:
                continue
            seen.add(newest.id)
            found.append(
                {
                    "id": newest.id,
                    "type": str(newest.type),
                    "content": newest.content,
                    "kinds": [k for k in intent_kinds(newest.content) if k in HEADLINE_INTENTS] or [kind],
                    "last_seen_at": newest.last_seen_at,
                }
            )
        return found


def _whole(days: Any) -> int | None:
    return int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) else None


def _days(moment: Any, now: datetime) -> int | None:
    if not isinstance(moment, datetime):
        return None
    return max(0, int((ensure_utc(now) - ensure_utc(moment)).total_seconds() // 86400))


def _plan(visible: CustomerFacts, subscription: dict[str, Any] | None) -> tuple[str | None, str | None, Any]:
    """The plan, and the statement it was read from — only when that statement is the one
    the plan fact cites. A reader who may not see the newest subscription memory would
    otherwise be shown an older plan statement as though it were current."""
    plan = visible.get("subscription.plan")
    if not plan:
        return None, None, None
    cited = visible.evidence.get("subscription.plan") or []
    if subscription and subscription.get("id") in cited:
        return str(plan), subscription.get("content"), subscription.get("last_seen_at")
    return str(plan), None, None


def _live_goals(goals: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Goals still in play, and ones reached recently enough to acknowledge."""
    kept = []
    for goal in goals:
        status = str(goal.get("status"))
        if status in LIVE_GOALS:
            kept.append(goal)
        elif status == "achieved":
            moment = goal.get("last_signal_at")
            if isinstance(moment, datetime) and ensure_utc(now) - ensure_utc(moment) <= ACHIEVED_RECENTLY:
                kept.append(goal)
    return kept[:GOALS]


def _last_conversation(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest conversation that ended with a summary — an open one is the one asking."""
    for item in sessions:
        if item.get("is_open") or not item.get("summary"):
            continue
        return {
            "id": item["id"],
            "agent": item.get("agent"),
            "summary": item["summary"],
            "closed_at": item.get("closed_at"),
            "turn_count": item.get("turn_count"),
        }
    return None


def _evidence(parts: BriefParts, step: dict[str, Any] | None, cautions: list[dict[str, Any]]) -> list[str]:
    """Every memory and goal id the brief rests on, once each."""
    ids: list[str] = []
    for item in (*parts.problems, *parts.preferences, *parts.intents, *parts.goals):
        if item.get("id") and item.get("content") != WITHHELD:
            ids.append(str(item["id"]))
    if step:
        ids.extend(str(ident) for ident in step.get("memory_ids") or [])
        ids.extend(str(ident) for ident in step.get("goal_ids") or [])
    for caution in cautions:
        ids.extend(str(ident) for ident in caution.get("evidence") or [])
    return list(dict.fromkeys(ids))
