"""A customer brief: decision-ready, not a data dump (§26 5.2).

"Brief me on Acme" is not "show me everything about Acme". A person about to call them —
or an agent about to reply — needs the situation in a sentence, what to raise and in what
order, what *not* to do, and the evidence behind each. Customer 360 (§12b) is the complete
record; the brief is the judgement about it.

Everything here is pure composition over parts the service gathered from the services that
own them — health, the forecast and its recommendations, goals, memories, lifecycle tracks,
what changed (§26 4.1) and guardrail previews (§26 3.2) — already shaped for the reader, so
nothing here can quote what the reader may not see.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.time import ensure_utc
from memory_engine.consolidation.rules import without_recurrence_note
from memory_engine.reasons import sentence
from nlp.entities import display_channel

WITHHELD = "[withheld]"
TALKING_POINTS = 6
# How much of "what changed" a talking point repeats; the rest is in its own section.
CHANGE_CLAUSES = 4
# A customer this long without an event is worth saying so about.
QUIET_DAYS = 14
# An achieved goal is worth acknowledging for a month.
RECENT_ACHIEVEMENT_DAYS = 30
# A plan change is news for a month; after that the plan is just the plan.
RECENT_PLAN_DAYS = 30

# How a caution names the action it is about.
ACTION_PHRASES = {
    "offer_upgrade": "offer an upgrade",
    "send_marketing": "send them marketing",
    "contact_customer": "reach out unprompted",
    "call_customer": "call them",
    "send_email": "email them",
    "offer_discount": "offer a discount",
    "issue_credit": "issue a credit",
    "close_ticket": "close their ticket",
    "request_review": "ask for a review or reference",
}
CAUTION_ACTIONS = tuple(ACTION_PHRASES)
# What each recommendation would have someone do, so the brief never advises the very thing
# one of its cautions forbids — "get on a call" for a customer who asked not to be called.
RECOMMENDATION_ACTIONS = {
    "expansion_offer": ("offer_upgrade",),
    "retention_outreach": ("call_customer",),
    "re_engage": ("contact_customer",),
    "ask_for_advocacy": ("request_review",),
}
# Approvals every customer needs. Policy, not something about *this* customer — a brief
# that repeated them for everyone would teach its reader to skip the section.
STANDING_RULES = frozenset({"money_requires_approval", "account_change_requires_approval"})

# The intents a brief leads with, most urgent first. The others (evaluation, integration,
# migration, purchase) are routine enough to leave to the full record.
HEADLINE_INTENTS = ("cancellation", "downgrade", "expansion", "renewal")
_INTENT_WORDS = {
    "cancellation": "said they may cancel",
    "downgrade": "talked about downgrading",
    "expansion": "talked about expanding",
    "renewal": "talked about their renewal",
}


@dataclass(slots=True)
class BriefParts:
    name: str
    now: datetime
    customer_since: datetime | None = None
    last_active: datetime | None = None
    health: dict[str, Any] | None = None  # score, band
    trajectory: str | None = None
    churn_risk: float | None = None
    plan: str | None = None
    plan_statement: str | None = None
    plan_changed_at: datetime | None = None
    plan_direction: str | None = None  # upgraded, downgraded, cancelled, renewed, started, changed
    previous_plan: str | None = None
    plan_changed_days: int | None = None
    lifecycle: list[dict[str, Any]] = field(default_factory=list)  # track, label, state, entered_at, reasons
    # Every open problem, counted over everything — a count is not a quote (§17c).
    open_problems: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)  # id, content, first_seen_at, evidence_count
    goals: list[dict[str, Any]] = field(default_factory=list)  # id, statement, status, progress, last_signal_at
    preferences: list[dict[str, Any]] = field(default_factory=list)  # id, content
    channel: str | None = None
    opt_outs: list[str] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)  # id, content, kinds
    risks: list[dict[str, Any]] = field(default_factory=list)  # key, label, rationale
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)  # key, action, rationale, priority
    conversation: dict[str, Any] | None = None  # summary, agent, closed_at
    changes: dict[str, Any] | None = None  # summary, label, items
    cautions: list[dict[str, Any]] = field(default_factory=list)  # action, decision, summary, rules, evidence
    # Open drift flags (§26 5.5): kind, stated, observed, summary, counts, memory_id.
    drift: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------- words


def _days(moment: Any, now: datetime) -> int | None:
    if not isinstance(moment, datetime):
        return None
    return max(0, int((ensure_utc(now) - ensure_utc(moment)).total_seconds() // 86400))


def _ago(days: int | None) -> str:
    if days is None:
        return "an unknown time"
    if days == 0:
        return "today"
    if days == 1:
        return "a day"
    if days < 14:
        return f"{days} days"
    if days < 60:
        return f"{days // 7} weeks"
    if days < 365:
        return f"{days // 30} months"
    years = days // 365
    return "over a year" if years == 1 else f"over {years} years"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _quote(text: Any, limit: int = 140) -> str:
    # A brief says how often a problem was reported itself; the note in the memory would
    # say it twice.
    words = " ".join(without_recurrence_note(str(text or "")).split())
    if words == WITHHELD:
        return "a statement you may not read"
    clipped = words if len(words) <= limit else words[: limit - 1].rstrip() + "…"
    return f"“{clipped.rstrip('.')}”"


def _words(value: Any) -> str:
    return str(value).replace("_", " ")


def _lower_first(text: str) -> str:
    """"The customer…" → "the customer…", leaving "SSO…" alone."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} or {items[-1]}"


def _day_words(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{_ago(days)} ago"


def _recent_change(parts: BriefParts) -> str | None:
    """The plan change worth saying, if it happened within the month."""
    if parts.plan_changed_days is None or parts.plan_changed_days > RECENT_PLAN_DAYS:
        return None
    if parts.plan_direction == "cancelled":
        return "cancelled"
    if parts.plan_direction in ("upgraded", "downgraded") and parts.previous_plan and parts.previous_plan != WITHHELD:
        return parts.plan_direction
    return None


def _intent(parts: BriefParts, kind: str) -> dict[str, Any] | None:
    return next((intent for intent in parts.intents if kind in (intent.get("kinds") or [])), None)


# ----------------------------------------------------------------- judgement


def headline(parts: BriefParts) -> str:
    """The situation in a few short sentences: health and direction, plan and tenure, what
    is open, what they said they intend, and a long silence if there is one."""
    bits: list[str] = []
    if parts.health:
        band = _words(parts.health.get("band") or "unknown")
        score = parts.health.get("score")
        bits.append(f"{band} ({float(score):.0f})" if isinstance(score, (int, float)) else band)
    if parts.trajectory and parts.trajectory != "steady":
        bits.append(_words(parts.trajectory))
    sentences = [f"{parts.name}: {', '.join(bits)}." if bits else f"{parts.name}."]

    tenure_days = _days(parts.customer_since, parts.now)
    tenure = None
    if tenure_days is not None:
        tenure = "a new customer" if tenure_days == 0 else f"customer for {_ago(tenure_days)}"
    if parts.plan and parts.plan != WITHHELD:
        name = parts.plan.title()
        change = _recent_change(parts)
        when = _day_words(parts.plan_changed_days or 0)
        if parts.plan_direction == "cancelled":
            # A cancelled plan is not the plan they are on.
            plan = f"Cancelled the {name} plan" + (f" {when}" if change else "")
        elif change:
            plan = f"On the {name} plan ({change} from {str(parts.previous_plan).title()} {when})"
        else:
            plan = f"On the {name} plan"
        sentences.append(plan + (f", {tenure}." if tenure else "."))
    elif tenure:
        sentences.append(tenure[0].upper() + tenure[1:] + ".")

    count = max(parts.open_problems, len(parts.problems))
    tail = [_plural(count, "open problem")] if count else ["no open problems"]
    kinds = {kind for intent in parts.intents for kind in intent.get("kinds") or []}
    said = next((kind for kind in HEADLINE_INTENTS if kind in kinds), None)
    if said is not None:
        tail.append(_INTENT_WORDS[said])
    joined = "; ".join(tail)
    sentences.append(joined[0].upper() + joined[1:] + ".")

    quiet = _days(parts.last_active, parts.now)
    if quiet is not None and quiet >= QUIET_DAYS:
        sentences.append(f"No activity in {_ago(quiet)}.")
    return " ".join(sentences)


def next_step(parts: BriefParts) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The most urgent recommendation no caution forbids, and the ones set aside for one."""
    refused = {
        str(caution.get("action")): str(caution.get("summary") or "")
        for caution in parts.cautions
        if caution.get("decision") == "deny"
    }
    set_aside: list[dict[str, Any]] = []
    for recommendation in parts.recommendations:
        blocked = next((action for action in RECOMMENDATION_ACTIONS.get(str(recommendation.get("key")), ()) if action in refused), None)
        if blocked is None:
            return recommendation, set_aside
        set_aside.append({"key": recommendation.get("key"), "action": recommendation.get("action"), "because": refused[blocked]})
    return None, set_aside


def talking_points(parts: BriefParts, step: dict[str, Any] | None = None) -> list[str]:
    """What to raise, most important first — one sentence each, each from the evidence."""
    points: list[str] = []
    problems = {str(item.get("id")): item for item in parts.problems}
    quoted: set[str] = set()
    leaving = _intent(parts, "cancellation")
    shrinking = None if leaving else _intent(parts, "downgrade")
    if leaving is not None:
        quoted.add(str(leaving.get("id")))
        # A threat filed as a problem ("we will cancel if the export keeps failing") is
        # both: say it once, with how long the problem behind it has been open.
        standing = _standing(problems.get(str(leaving.get("id"))), parts.now)
        points.append(
            f"They said they may leave: {_quote(leaving.get('content'))}{standing}. Acknowledge it before anything else."
        )
    elif shrinking is not None:
        quoted.add(str(shrinking.get("id")))
        standing = _standing(problems.get(str(shrinking.get("id"))), parts.now)
        points.append(
            f"They talked about downgrading: {_quote(shrinking.get('content'))}{standing}. "
            "Find out what no longer earns its price."
        )

    # A plan given up in the last month is the first thing a person should know about.
    change = _recent_change(parts)
    if change in ("downgraded", "cancelled") and parts.plan:
        when = _day_words(parts.plan_changed_days or 0)
        what = (
            f"They cancelled the {parts.plan.title()} plan {when}"
            if change == "cancelled"
            else f"They downgraded from {str(parts.previous_plan).title()} to {parts.plan.title()} {when}"
        )
        said = f": {_quote(parts.plan_statement)}" if parts.plan_statement else ""
        points.append(f"{what}{said}.")

    # A problem drift says may be fixed is asked about, not quoted as still open.
    quoted |= {str(flag.get("memory_id")) for flag in parts.drift if flag.get("kind") == "quiet_problem"}
    # The problems that weigh most: reported most often, then open longest.
    pressing = sorted(
        (item for item in parts.problems if str(item.get("id")) not in quoted),
        key=lambda item: (-int(item.get("evidence_count") or 1), _sort_time(item.get("first_seen_at"))),
    )
    for problem in pressing[:2]:
        times = int(problem.get("evidence_count") or 1)
        age = _ago(_days(problem.get("first_seen_at"), parts.now))
        again = f", reported {times} times" if times > 1 else ""
        opened = "Opened today" if age == "today" else f"Still open after {age}"
        points.append(f"{opened}{again}: {_quote(problem.get('content'))}.")

    points.extend(drift_points(parts))

    if parts.changes and parts.changes.get("items") and parts.changes.get("summary"):
        points.append(_short(str(parts.changes["summary"])))

    for goal in parts.goals:
        status = str(goal.get("status"))
        statement = _quote(goal.get("statement"))
        if status == "stalled":
            quiet = _ago(_days(goal.get("last_signal_at"), parts.now))
            points.append(f"Their goal {statement} has had no progress in {quiet} — ask how it is going.")
        elif status == "achieved" and (_days(goal.get("last_signal_at"), parts.now) or 0) <= RECENT_ACHIEVEMENT_DAYS:
            points.append(f"They achieved their goal {statement} — worth acknowledging.")

    if leaving is None and shrinking is None:
        growing = _intent(parts, "expansion")
        renewing = _intent(parts, "renewal")
        if growing is not None:
            points.append(f"They talked about expanding: {_quote(growing.get('content'))}.")
        elif renewing is not None:
            points.append(f"Their renewal came up: {_quote(renewing.get('content'))}.")

    if step and step.get("action"):
        action = str(step["action"]).rstrip(".")
        rationale = str(step.get("rationale") or "").rstrip(".")
        points.append(f"Next step — {action}" + (f" ({_lower_first(rationale)})." if rationale else "."))

    if parts.channel and parts.channel != WITHHELD:
        points.append(f"They prefer {parts.channel}.")
    return [point for point in points if point][:TALKING_POINTS]


def _standing(problem: dict[str, Any] | None, now: datetime) -> str:
    """" (open 2 weeks, reported 3 times)" for a problem a statement also is."""
    if problem is None:
        return ""
    days = _days(problem.get("first_seen_at"), now)
    times = int(problem.get("evidence_count") or 1)
    bits = [] if days is None or days == 0 else [f"open {_ago(days)}"]
    if times > 1:
        bits.append(f"reported {times} times")
    return f" ({', '.join(bits)})" if bits else ""


def _short(summary: str, clauses: int = CHANGE_CLAUSES) -> str:
    """The summary cut to its first few clauses: "Since …: a; b; c; d — and 2 more changes."."""
    lead, colon, rest = summary.partition(": ")
    if not colon:
        return summary
    items = [item for item in rest.rstrip(".").split("; ") if item]
    if len(items) <= clauses:
        return summary
    more = len(items) - clauses
    return f"{lead}: {'; '.join(items[:clauses])} — and {more} more change{'' if more == 1 else 's'}."


def drift_points(parts: BriefParts) -> list[str]:
    """What evidence says may be out of date, each as a question to ask — never as fact."""
    points: list[str] = []
    for flag in parts.drift:
        kind = flag.get("kind")
        counts = flag.get("counts") or {}
        stated = str(flag.get("stated") or "")
        observed = str(flag.get("observed") or "")
        if kind == "channel" and observed:
            share = (
                f"{counts['observed']} of their {counts['total']} contacts"
                if counts.get("observed") and counts.get("total")
                else "most of their contacts"
            )
            points.append(
                f"They said they prefer {stated}, but {share} since came through {display_channel(observed)} "
                "— ask which they prefer now."
            )
        elif kind == "plan" and observed:
            events = counts.get("events")
            billed = f"their last {events} billing events were" if events else "billing says it is"
            points.append(
                f"Memory says the {stated.title()} plan, but {billed} for {observed.title()} — check which plan they are on."
            )
        elif kind == "quiet_problem":
            quiet = counts.get("quiet_days")
            ago = f" in {_ago(int(quiet))}" if isinstance(quiet, (int, float)) else " for a while"
            points.append(f"{_quote(stated)} has not come up{ago} while they stayed active — ask whether it is fixed.")
        elif kind == "usage":
            quiet = counts.get("quiet_days")
            ago = f" in {_ago(int(quiet))}" if isinstance(quiet, (int, float)) else " for a while"
            points.append(f"They have not used the {stated}{ago} while staying active — ask what changed.")
    return points


def _sort_time(value: Any) -> float:
    if isinstance(value, datetime):
        return ensure_utc(value).timestamp()
    return 0.0


def caution_lines(cautions: Sequence[dict[str, Any]], *, open_problems: int = 0) -> list[dict[str, Any]]:
    """What not to do, one line per reason.

    Four contact actions refused because the customer asked not to be contacted are one
    caution naming all four, not four cautions. Standing approval policy (every discount
    needs a person) is left out: it says nothing about this customer.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for caution in cautions:
        decision = str(caution.get("decision") or "")
        if decision not in ("deny", "require_approval"):
            continue
        rules = [str(rule) for rule in caution.get("rules") or [] if rule]
        if rules and all(rule in STANDING_RULES for rule in rules):
            continue
        summary = str(caution.get("summary") or "").strip()
        if not summary:
            continue
        key = (decision, summary)
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "action": caution.get("action"),
                "actions": [caution.get("action")],
                "decision": decision,
                "summary": summary,
                "rules": rules,
                "evidence": list(caution.get("evidence") or []),
            }
            continue
        entry["actions"].append(caution.get("action"))
        entry["rules"] = list(dict.fromkeys([*entry["rules"], *rules]))
        entry["evidence"] = list(dict.fromkeys([*entry["evidence"], *(caution.get("evidence") or [])]))

    out: list[dict[str, Any]] = []
    # Refusals before approvals: "don't" is the stronger instruction.
    for entry in sorted(grouped.values(), key=lambda item: item["decision"] != "deny"):
        phrases = _join([ACTION_PHRASES.get(str(action), _words(action)) for action in entry["actions"]])
        reason = _lower_first(entry["summary"])
        if entry["rules"] == ["unresolved_problem_blocks_closing"] and entry["decision"] == "require_approval":
            # The check's own words talk about "the request", which a brief has none of.
            still = f"{_plural(open_problems, 'problem')} still open" if open_problems else "problems are still open"
            entry["text"] = f"Confirm the problem is fixed before you {phrases}: {still}."
        elif entry["decision"] == "deny":
            entry["text"] = f"Don't {phrases}: {reason}"
        else:
            entry["text"] = f"Ask a person before you {phrases}: {reason}"
        out.append(entry)
    return out


def compose(parts: BriefParts) -> dict[str, Any]:
    """The brief's judgement: headline, talking points, cautions, and the next step."""
    step, set_aside = next_step(parts)
    return {
        "headline": headline(parts),
        "talking_points": talking_points(parts, step),
        "cautions": caution_lines(parts.cautions, open_problems=max(parts.open_problems, len(parts.problems))),
        "next_step": step,
        "set_aside": set_aside,
    }


# -------------------------------------------------------------------- markdown


def markdown(brief: dict[str, Any]) -> str:
    """The brief as a page a person — or a model's context window — can read."""
    customer = brief.get("customer") or {}
    situation = brief.get("situation") or {}
    generated = brief.get("generated_at")
    lines = [f"# {customer.get('name') or customer.get('external_id')}", "", brief.get("headline") or ""]

    health = situation.get("health") or {}
    facts: list[str] = []
    if health.get("score") is not None:
        risk = health.get("churn_risk")
        trend = f", {_words(health['trajectory'])}" if health.get("trajectory") else ""
        churn = f"; churn risk {float(risk):.0%}" if isinstance(risk, (int, float)) else ""
        facts.append(f"**Health:** {float(health['score']):.0f}/100 ({_words(health.get('band'))}){trend}{churn}")
    plan = situation.get("plan") or {}
    if plan.get("name"):
        statement = f" — {_quote(plan['statement'])}" if plan.get("statement") else ""
        facts.append(f"**Plan:** {str(plan['name']).title()}{statement}")
    tracks = situation.get("lifecycle") or []
    if tracks:
        described = " · ".join(f"{track['label']}: {_words(track['state'])}" for track in tracks if track.get("state"))
        reasons = next((track.get("reasons") for track in tracks if track.get("reasons")), None)
        facts.append(f"**Lifecycle:** {described}" + (f" — {'; '.join(reasons[:3])}" if reasons else ""))
    since = customer.get("customer_since")
    active = customer.get("last_active_at")
    if isinstance(generated, datetime) and (isinstance(since, datetime) or isinstance(active, datetime)):
        known = []
        if isinstance(since, datetime):
            known.append(f"customer since {ensure_utc(since):%Y-%m-%d}")
        if isinstance(active, datetime):
            known.append(f"last active {_when(active, generated)}")
        facts.append(f"**Account:** {', '.join(known)}")
    if facts:
        lines += ["", "## Situation", *[f"- {fact}" for fact in facts]]

    if brief.get("talking_points"):
        lines += ["", "## Talk about", *[f"- {point}" for point in brief["talking_points"]]]
    if brief.get("cautions"):
        lines += ["", "## Don't", *[f"- {caution['text']}" for caution in brief["cautions"]]]

    issues = brief.get("open_issues") or []
    if issues:
        lines += ["", "## Open issues"]
        for issue in issues:
            times = issue.get("times_reported") or 1
            extra = f", reported {times} times" if times > 1 else ""
            age = issue.get("age_days")
            opened = "opened today" if age == 0 else f"open {_ago(age)}"
            lines.append(f"- {_quote(issue.get('content'))} — {opened}{extra}")
        more = int(situation.get("open_problems") or 0) - len(issues)
        if more > 0:
            lines.append(f"- …and {more} more")
    goals = brief.get("goals") or []
    if goals:
        lines += ["", "## Goals"]
        for goal in goals:
            progress = goal.get("progress")
            share = f", {float(progress):.0%}" if isinstance(progress, (int, float)) and progress else ""
            lines.append(f"- {_quote(goal.get('statement'))} ({_words(goal.get('status'))}{share})")
    preferences = brief.get("preferences") or {}
    prefer: list[str] = []
    if preferences.get("channel"):
        prefer.append(f"Prefers {preferences['channel']}.")
    for opt_out in preferences.get("opt_outs") or []:
        words = str(opt_out.get("words") or opt_out.get("kind") or "")
        if words:
            prefer.append(f"{words[:1].upper()}{words[1:]}.")
    prefer += [_quote(item.get("content")) for item in (preferences.get("statements") or [])[:4]]
    if prefer:
        lines += ["", "## What they prefer", *[f"- {item}" for item in prefer]]

    drift = brief.get("drift") or []
    if drift:
        lines += ["", "## Possibly out of date"]
        lines += [f"- {flag.get('summary')}" for flag in drift]

    signals = [("Risk", item) for item in (brief.get("risks") or [])[:3]]
    signals += [("Opportunity", item) for item in (brief.get("opportunities") or [])[:2]]
    if signals:
        lines += ["", "## Signals"]
        for kind, signal in signals:
            rationale = f" — {signal['rationale']}" if signal.get("rationale") else ""
            lines.append(f"- {kind}: {signal.get('label') or _words(signal.get('key'))}{rationale}")

    changes = brief.get("recent_changes") or {}
    if changes.get("items"):
        lines += ["", "## What changed", changes.get("summary") or ""]
        for item in changes["items"][:6]:
            when = item.get("detected_at")
            day = f"{ensure_utc(when):%Y-%m-%d} " if isinstance(when, datetime) else f"{str(when or '')[:10]} "
            lines.append(f"- {day.lstrip()}{item.get('title')}")
    conversation = brief.get("last_conversation")
    if conversation and conversation.get("summary"):
        closed = conversation.get("closed_at")
        when = _when(closed, generated) if isinstance(closed, datetime) and isinstance(generated, datetime) else None
        meta = ", ".join(part for part in (conversation.get("agent"), when) if part)
        summary = str(conversation["summary"])
        if summary == WITHHELD:
            summary = "_The summary quotes something your key may not read._"
        lines += ["", "## Last conversation", summary + (f" ({meta})" if meta else "")]
    step = brief.get("next_step")
    if step and step.get("action"):
        rationale = f" — {step['rationale']}" if step.get("rationale") else ""
        lines += ["", "## Next step", f"**{step['action']}**{rationale}"]
    for item in brief.get("set_aside") or []:
        lines.append(f"_Set aside: {item['action']} — {_lower_first(str(item['because']))}_")
    if brief.get("withheld"):
        count = int(brief["withheld"])
        lines += ["", f"_{_plural(count, 'memory').replace('memorys', 'memories')} withheld from this brief for your key._"]
    return "\n".join(lines).strip() + "\n"


def _when(moment: datetime, now: datetime) -> str:
    days = _days(moment, now)
    if days is None:
        return ""
    if days == 0:
        return "today"
    return f"{_ago(days)} ago"


def channel_name(channel: Any) -> str | None:
    """A channel as people write it: the fact is "whatsapp", the brief says "WhatsApp"."""
    if not channel or channel == WITHHELD:
        return channel or None
    return display_channel(channel)


def opt_out_words(kinds: Sequence[str]) -> list[dict[str, str]]:
    """Each opt-out with the words for it: "asked not to be called"."""
    return [{"kind": kind, "words": sentence("preferences.opt_outs", [kind]) or kind} for kind in kinds]
