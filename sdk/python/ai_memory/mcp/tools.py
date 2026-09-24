"""The tools an MCP client sees, each a thin, well-described call through the SDK.

Descriptions are written for the model that will choose between them: what the tool is
for, when to prefer it, and what comes back. Every result is text a model can read *and*
structured data a program can use; failures come back as tool errors the model can react
to ("that customer does not exist"), never as protocol errors that end the conversation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MEMORY_TYPES = [
    "fact",
    "preference",
    "problem",
    "goal",
    "behavior",
    "relationship",
    "subscription",
    "feedback",
    "intent",
    "summary",
]
SECTIONS_360 = [
    "health",
    "subscription",
    "active_problems",
    "goals",
    "preferences",
    "recent_activity",
    "important_memories",
    "relationships",
    "risk_signals",
    "recommended_actions",
    "recent_conversations",
]

CUSTOMER_ID = {"type": "string", "description": "Your id for the customer (their external id) or Memora's cus_… id."}


class ToolInputError(ValueError):
    """Arguments a tool cannot use — reported to the model as a tool error."""


@dataclass(slots=True)
class ToolResult:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False


@dataclass(slots=True, frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Any, dict[str, Any]], ToolResult]
    read_only: bool = True

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": self.read_only,
                "openWorldHint": False,
            },
        }


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _required(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"'{name}' is required.")
    return value.strip()


def _limit(args: dict[str, Any], default: int, maximum: int) -> int:
    value = args.get("limit", default)
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError) as exc:
        raise ToolInputError("'limit' must be a number.") from exc


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


# --------------------------------------------------------------------- handlers


def ask_memory(client: Any, args: dict[str, Any]) -> ToolResult:
    customer = _required(args, "customer_id")
    result = client.query(customer, _required(args, "question"), limit=_limit(args, 8, 20), session_id=args.get("session_id"))
    cited = [memory for memory in result.memories if memory.score is not None][:5] or result.memories[:5]
    text = result.answer or "Nothing in memory answers that."
    if cited:
        text += "\n\nFrom memory:\n" + _bullets([f"[{m.type}] {m.content} ({m.id})" for m in cited], "")
    if result.run_id:
        text += f"\n\nRun: {result.run_id} (use explain_answer to see why)"
    return ToolResult(
        text=text,
        data={
            "answer": result.answer,
            "confidence": result.confidence,
            "run_id": result.run_id,
            "memories": [{"id": m.id, "type": m.type, "content": m.content, "score": m.score} for m in result.memories],
        },
    )


def search_memory(client: Any, args: dict[str, Any]) -> ToolResult:
    types = args.get("types") or None
    if types is not None and (not isinstance(types, list) or any(t not in MEMORY_TYPES for t in types)):
        raise ToolInputError(f"'types' must be a list drawn from: {', '.join(MEMORY_TYPES)}.")
    memories = client.search(
        _required(args, "query"), customer_id=args.get("customer_id"), limit=_limit(args, 10, 50), types=types
    )
    lines = [f"[{m.type}] {m.content} (id {m.id}, score {m.score if m.score is not None else '-'})" for m in memories]
    return ToolResult(
        text=_bullets(lines, "No memories matched."),
        data={"memories": [{"id": m.id, "type": m.type, "content": m.content, "score": m.score, "retrieved_by": m.retrieved_by} for m in memories]},
    )


def customer_360(client: Any, args: dict[str, Any]) -> ToolResult:
    include = args.get("include") or None
    if include is not None and (not isinstance(include, list) or any(item not in SECTIONS_360 for item in include)):
        raise ToolInputError(f"'include' must be a list drawn from: {', '.join(SECTIONS_360)}.")
    view = client.customer_360(_required(args, "customer_id"), include=include)
    parts = [view.summary]
    for name, value in view.sections.items():
        if isinstance(value, list):
            rendered = [_render_item(item) for item in value[:8]]
            parts.append(f"{name.replace('_', ' ').capitalize()}:\n{_bullets(rendered, '- none')}")
        elif isinstance(value, dict):
            parts.append(f"{name.replace('_', ' ').capitalize()}: {_render_item(value)}")
    if view.withheld:
        parts.append(f"({view.withheld} memories are not visible to this key.)")
    return ToolResult(
        text="\n\n".join(part for part in parts if part),
        data={"customer": view.customer, "summary": view.summary, "sections": view.sections, "withheld": view.withheld},
    )


def customer_brief(client: Any, args: dict[str, Any]) -> ToolResult:
    """A readable page composed from the 360 — the same facts, arranged for a person."""
    customer = _required(args, "customer_id")
    view = client.customer_360(customer)
    name = view.customer.get("name") or view.customer.get("external_id") or customer
    health = view.sections.get("health") or {}
    lines = [f"# {name}", "", view.summary]
    if health:
        # The explanation already states the score; the bare numbers are the fallback.
        score_line = f"{float(health.get('score', 0)):.0f}/100 ({health.get('band')})"
        lines += ["", f"**Health:** {health.get('explanation') or score_line}"]
    subscription = view.sections.get("subscription")
    if subscription:
        lines.append(f"**Plan:** {subscription.get('content')}")
    for title, key in (
        ("Open problems", "active_problems"),
        ("What they care about", "preferences"),
        ("Goals", "goals"),
    ):
        items = view.sections.get(key) or []
        if items:
            lines += ["", f"## {title}", _bullets([_render_item(item) for item in items[:6]], "")]
    conversations = view.sections.get("recent_conversations") or []
    if conversations and conversations[0].get("summary"):
        summary = str(conversations[0]["summary"])
        if summary == "[withheld]":
            summary = "A summary was written, but this key may not read it."
        lines += ["", "## Last conversation", summary]
    actions = view.sections.get("recommended_actions") or []
    if actions:
        lines += ["", "## Recommended next step", f"{actions[0].get('action')} — {actions[0].get('rationale')}"]
    return ToolResult(text="\n".join(lines).strip(), data={"customer": view.customer, "summary": view.summary, "sections": view.sections})


def customer_timeline(client: Any, args: dict[str, Any]) -> ToolResult:
    entries = client.timeline(_required(args, "customer_id"), limit=_limit(args, 30, 100))
    lines = [
        f"{str(entry.get('occurred_at', ''))[:16]} [{entry.get('kind')}] {entry.get('title')}: {entry.get('detail') or ''}".rstrip(": ")
        for entry in entries
    ]
    return ToolResult(text=_bullets(lines, "Nothing has happened yet."), data={"entries": entries})


def customer_changes(client: Any, args: dict[str, Any]) -> ToolResult:
    customer = _required(args, "customer_id")
    since = str(args.get("since") or "7d").strip()
    types = args.get("types")
    if types is not None and not isinstance(types, list):
        raise ToolInputError("'types' is a list, e.g. [\"problem\", \"subscription\"].")
    changes = client.changes(
        customer,
        since=since,
        agent=args.get("agent"),
        types=types,
        order="importance" if args.get("most_important_first") else "time",
        limit=_limit(args, 20, 100),
    )
    lines = []
    for change in changes.changes:
        line = f"{change.detected_at[:10]} [{change.type}] {change.title}"
        if change.before and change.before not in change.title:
            line += f" (was: {change.before})"
        if change.reasons:
            line += f" — because {'; '.join(change.reasons)}"
        lines.append(line)
    text = changes.summary
    if lines:
        text += "\n" + _bullets(lines, "")
    then, now = changes.then.get("description"), changes.now.get("description")
    if then and now:
        text += f"\n\nThen: {then}\nNow: {now}"
    if changes.withheld:
        text += f"\n\n{changes.withheld} change(s) concern memories you may not read."
    if changes.note:
        text += f"\n\n{changes.note}"
    return ToolResult(text=text, data=changes.raw)


def get_health(client: Any, args: dict[str, Any]) -> ToolResult:
    customer = _required(args, "customer_id")
    health = client.health(customer)
    signals = client.signals(customer, series=False)
    text = (
        f"Health {health.score:.0f}/100 ({health.band}), churn risk {health.churn_risk:.0%}. {health.explanation}\n"
        f"Trajectory: {signals.trajectory}. {signals.headline}"
    )
    return ToolResult(
        text=text.strip(),
        data={
            "score": health.score,
            "band": health.band,
            "churn_risk": health.churn_risk,
            "factors": health.factors,
            "trajectory": signals.trajectory,
            "headline": signals.headline,
            "signals": [{"key": s.key, "label": s.label, "rationale": s.rationale} for s in signals.signals],
        },
    )


def get_goals(client: Any, args: dict[str, Any]) -> ToolResult:
    goals = client.goals(_required(args, "customer_id"), status=args.get("status"))
    lines = [f"{goal.statement} — {goal.status}, {goal.progress:.0%} (id {goal.id})" for goal in goals]
    return ToolResult(
        text=_bullets(lines, "No goals recorded."),
        data={"goals": [{"id": g.id, "statement": g.statement, "status": g.status, "progress": g.progress} for g in goals]},
    )


def get_recommendations(client: Any, args: dict[str, Any]) -> ToolResult:
    actions = client.recommendations(_required(args, "customer_id"))
    lines = [f"[{a.priority}] {a.action} — {a.rationale}" for a in actions]
    return ToolResult(
        text=_bullets(lines, "Nothing needs doing."),
        data={"recommendations": [{"key": a.key, "action": a.action, "rationale": a.rationale, "priority": a.priority, "memory_ids": a.memory_ids} for a in actions]},
    )


def check_action(client: Any, args: dict[str, Any]) -> ToolResult:
    request = args.get("request") or {}
    if not isinstance(request, dict):
        raise ToolInputError("'request' must be an object, e.g. {\"channel\": \"email\"}.")
    check = client.check_action(
        _required(args, "customer_id"),
        _required(args, "action"),
        request,
        approval_id=args.get("approval_id"),
        session_id=args.get("session_id"),
    )
    verdict = {"allow": "ALLOWED", "require_approval": "NEEDS APPROVAL", "deny": "DENIED"}.get(check.decision, check.decision)
    text = f"{verdict}: {check.summary}"
    if len(check.reasons) > 1:
        text += "\n" + _bullets([f"{r.get('rule')} ({r.get('decision')}): {r.get('explanation')}" for r in check.reasons], "")
    if check.approval is not None:
        text += (
            f"\n\nApproval request {check.approval.id} is {check.approval.status}. Do not take the action until a "
            "person approves it; then call check_action again with approval_id."
        )
    return ToolResult(
        text=text,
        data={
            "decision": check.decision,
            "allowed": check.allowed,
            "summary": check.summary,
            "reasons": check.reasons,
            "evidence": check.evidence,
            "approval": {"id": check.approval.id, "status": check.approval.status} if check.approval else None,
            "check_id": check.id,
        },
    )


_MARKS = {"cited": "✓", "given": "✓", "not_cited": "○"}


_VERDICTS = {"allowed": "ALLOWED", "pending_approval": "NEEDS APPROVAL", "denied": "DENIED"}


def _action_result(action: Any, lead: str | None = None) -> ToolResult:
    verdict = _VERDICTS.get(action.status, action.status.upper())
    lines = [lead or f"{verdict}: {action.summary}"]
    lines.extend(
        f"- {reason.get('rule')} ({reason.get('decision')}): {reason.get('explanation')}"
        for reason in action.reasons
        if reason.get("decision") != "allow"
    )
    lines.append(f"action_id {action.id}. {action.next_step}")
    return ToolResult(
        text="\n".join(lines),
        data={
            "action_id": action.id,
            "status": action.status,
            "decision": action.decision,
            "summary": action.summary,
            "next_step": action.next_step,
            "reasons": action.reasons,
            "approval": {"id": action.approval.id, "status": action.approval.status} if action.approval else None,
        },
    )


def request_action(client: Any, args: dict[str, Any]) -> ToolResult:
    request = args.get("request") or {}
    if not isinstance(request, dict):
        raise ToolInputError("'request' must be an object, e.g. {\"amount\": 25}.")
    action = client.request_action(
        _required(args, "customer_id"),
        _required(args, "action"),
        request,
        idempotency_key=args.get("idempotency_key"),
        session_id=args.get("session_id"),
    )
    return _action_result(action)


def proceed_action(client: Any, args: dict[str, Any]) -> ToolResult:
    action = client.proceed_action(_required(args, "action_id"))
    if action.status == "pending_approval":
        return _action_result(action, "STILL WAITING: no person has decided yet.")
    return _action_result(action)


def report_action(client: Any, args: dict[str, Any]) -> ToolResult:
    outcome = args.get("outcome") or "done"
    if outcome not in ("done", "failed", "cancelled"):
        raise ToolInputError("'outcome' is done, failed or cancelled.")
    action = client.complete_action(
        _required(args, "action_id"), outcome, note=args.get("note"), external_ref=args.get("external_ref")
    )
    return ToolResult(
        text=f"Recorded: {action.action} {action.status}.",
        data={"action_id": action.id, "status": action.status},
    )


def explain_answer(client: Any, args: dict[str, Any]) -> ToolResult:
    trace = client.run_trace(_required(args, "run_id"))
    lines = list(trace.narrative)
    if trace.given:
        lines.append("Given:")
        for item in trace.given:
            words = item.get("content_then") or "(a memory you may not read)"
            line = f"{_MARKS.get(item.get('verdict'), '·')} {item.get('why')} “{_clip(words)}”"
            changed = item.get("changed_since") or []
            if changed and item.get("content_now"):
                line += f" — since changed, now reads “{_clip(item['content_now'])}”"
            lines.append(line)
    if trace.ignored:
        lines.append("Not given:")
        for item in trace.ignored:
            words = item.get("content") if item.get("visible") and item.get("content") else "(a memory you may not read)"
            lines.append(f"✗ “{_clip(words)}” — {item.get('why')}")
    return ToolResult(
        text="\n".join(lines) or "No explanation was recorded for that run.",
        data={
            "run": {"id": trace.run.id, "query": trace.run.query, "answer": trace.run.answer},
            "given": trace.given,
            "ignored": trace.ignored,
            "decision": trace.decision,
            "held_back": trace.held_back,
            "recorded": trace.recorded,
        },
    )


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def remember(client: Any, args: dict[str, Any]) -> ToolResult:
    memory_type = args.get("type") or "fact"
    if memory_type not in MEMORY_TYPES:
        raise ToolInputError(f"'type' must be one of: {', '.join(MEMORY_TYPES)}.")
    memory = client.remember(_required(args, "customer_id"), _required(args, "content"), type=memory_type)
    return ToolResult(text=f"Remembered ({memory.type}, id {memory.id}): {memory.content}", data={"id": memory.id, "type": memory.type, "content": memory.content})


def _render_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    for key in ("content", "statement", "action", "summary"):
        if item.get(key):
            text = str(item[key])
            status = item.get("status")
            return f"{text} ({status})" if status and key == "statement" else text
    if "score" in item and "band" in item:
        return f"{float(item['score']):.0f}/100 ({item['band']})"
    return ", ".join(f"{key}={value}" for key, value in list(item.items())[:4])


TOOLS: tuple[Tool, ...] = (
    Tool(
        "ask_memory",
        "Ask about a customer",
        "Answer a question about one customer from everything Memora remembers about them, and cite the memories used. "
        "Use this for specific questions (\"has Acme had billing problems?\", \"what plan are they on?\"). "
        "Returns the answer, the cited memories and a run_id for explain_answer.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "question": {"type": "string", "description": "The question, in plain language."},
                "session_id": {"type": "string", "description": "Optional agent session to file this under."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            ["customer_id", "question"],
        ),
        ask_memory,
    ),
    Tool(
        "search_memory",
        "Search memory",
        "Find memories that match a phrase, across one customer or all of them. Use this to look things up rather "
        "than to answer a question; filter by memory type when you only want problems, preferences and so on.",
        _schema(
            {
                "query": {"type": "string"},
                "customer_id": {**CUSTOMER_ID, "description": "Limit to one customer. Omit to search every customer."},
                "types": {"type": "array", "items": {"type": "string", "enum": MEMORY_TYPES}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            ["query"],
        ),
        search_memory,
    ),
    Tool(
        "customer_360",
        "Customer 360",
        "Everything worth knowing about a customer in one call: health, plan, open problems, preferences, goals, "
        "relationships, risk signals, recommended actions and recent conversations. Use before replying to a customer.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "include": {"type": "array", "items": {"type": "string", "enum": SECTIONS_360}, "description": "Only these sections."},
            },
            ["customer_id"],
        ),
        customer_360,
    ),
    Tool(
        "customer_brief",
        "Brief me on a customer",
        "A short, readable briefing on a customer — who they are, how healthy the relationship is, what is open, "
        "what they care about, the last conversation and the recommended next step. Use before a call or meeting.",
        _schema({"customer_id": CUSTOMER_ID}, ["customer_id"]),
        customer_brief,
    ),
    Tool(
        "customer_changes",
        "What changed",
        "What changed about a customer since a moment — problems opened and resolved, plan and preference changes "
        "with before and after, lifecycle moves with the reasons, health crossing a band, goals, intents, signals and "
        "activity — plus what the customer looked like then and now. Use since=last_session to catch up on everything "
        "since the last conversation before replying.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "since": {
                    "type": "string",
                    "description": "A span (7d, 12h, 2w, 3mo), an ISO date, a snapshot id, last_session or last_run. Default 7d.",
                },
                "agent": {"type": "string", "description": "With last_session/last_run: only this agent's."},
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "subscription", "lifecycle", "health", "risk", "trajectory", "problem", "intent",
                            "preference", "goal", "feedback", "relationship", "fact", "memory", "signal", "activity",
                        ],
                    },
                },
                "most_important_first": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            ["customer_id"],
        ),
        customer_changes,
    ),
    Tool(
        "customer_timeline",
        "Customer timeline",
        "The customer's events and memories in time order, newest first.",
        _schema(
            {"customer_id": CUSTOMER_ID, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30}},
            ["customer_id"],
        ),
        customer_timeline,
    ),
    Tool(
        "get_health",
        "Customer health",
        "The customer's health score and band with the factors behind it, their churn risk, and where they are heading.",
        _schema({"customer_id": CUSTOMER_ID}, ["customer_id"]),
        get_health,
    ),
    Tool(
        "get_goals",
        "Customer goals",
        "What the customer said they were trying to achieve, and how far each goal has got.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "status": {"type": "string", "enum": ["open", "progressing", "stalled", "achieved", "abandoned"]},
            },
            ["customer_id"],
        ),
        get_goals,
    ),
    Tool(
        "get_recommendations",
        "Recommended actions",
        "What to do about this customer next, most urgent first, each with its rationale and evidence.",
        _schema({"customer_id": CUSTOMER_ID}, ["customer_id"]),
        get_recommendations,
    ),
    Tool(
        "check_action",
        "Check before acting",
        "Ask whether you may take an action for a customer BEFORE taking it — offering an upgrade, contacting them on "
        "a channel, giving a discount or credit, closing a ticket, changing their plan. Returns ALLOWED, NEEDS APPROVAL "
        "or DENIED with the reason. Never take a denied action; for one that needs approval, wait for a person.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "action": {
                    "type": "string",
                    "description": "e.g. offer_upgrade, contact_customer, send_email, offer_discount, issue_credit, process_refund, close_ticket, downgrade_plan",
                },
                "request": {
                    "type": "object",
                    "description": "Details the rules read: channel, amount, topic, plan, and anything else.",
                    "additionalProperties": True,
                },
                "approval_id": {"type": "string", "description": "An approved request to redeem for exactly this action."},
                "session_id": {"type": "string"},
            },
            ["customer_id", "action"],
        ),
        check_action,
        read_only=False,
    ),
    Tool(
        "request_action",
        "Request an action",
        "Ask to take an action for a customer and have it recorded — the approval gateway. Use this rather than "
        "check_action when you are about to act: it applies the customer's opt-outs, the project's rules and its "
        "automatic approval limits, and files a request for a person when one is needed. Returns ALLOWED (act, then "
        "call report_action), NEEDS APPROVAL (do not act; call proceed_action later) or DENIED (never act).",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "action": {
                    "type": "string",
                    "description": "e.g. process_refund, issue_credit, offer_discount, contact_customer, send_email, call_customer, offer_upgrade, close_ticket",
                },
                "request": {
                    "type": "object",
                    "description": "Details the rules read: amount, channel, topic, plan, reply (true when answering the customer's own message).",
                    "additionalProperties": True,
                },
                "idempotency_key": {"type": "string", "description": "Your id for this action; retrying with it returns the same action."},
                "session_id": {"type": "string"},
            },
            ["customer_id", "action"],
        ),
        request_action,
        read_only=False,
    ),
    Tool(
        "proceed_action",
        "Proceed with an approved action",
        "After request_action said NEEDS APPROVAL: check whether a person has decided and, if they approved, go ahead "
        "(the rules are run again on today's facts). Returns ALLOWED, DENIED, or STILL WAITING.",
        _schema({"action_id": {"type": "string"}}, ["action_id"]),
        proceed_action,
        read_only=False,
    ),
    Tool(
        "report_action",
        "Report an action's outcome",
        "After taking an ALLOWED action, report what happened — done, failed or cancelled — so the customer's action "
        "history (which later rules read, e.g. a monthly limit on credits) reflects what really happened.",
        _schema(
            {
                "action_id": {"type": "string"},
                "outcome": {"type": "string", "enum": ["done", "failed", "cancelled"], "default": "done"},
                "note": {"type": "string"},
                "external_ref": {"type": "string", "description": "Your system's id for what was done, e.g. a refund id."},
            },
            ["action_id"],
        ),
        report_action,
        read_only=False,
    ),
    Tool(
        "explain_answer",
        "Explain an answer",
        "Why an earlier answer or briefing said what it said: the memories it was given (✓ cited, ○ retrieved but "
        "not used) as they were then and are now, and the ones it was NOT given with the reason — ranked below the "
        "cut, over the token budget, superseded by a newer memory, expired, restricted or outside its profile — plus "
        "the decision and its confidence. Pass the run_id from ask_memory.",
        _schema({"run_id": {"type": "string"}}, ["run_id"]),
        explain_answer,
    ),
    Tool(
        "remember",
        "Remember a fact",
        "Record something you learned about the customer that should be remembered for next time — a verified fact, "
        "a stated preference, a problem, a goal. Needs a key with memory:write.",
        _schema(
            {
                "customer_id": CUSTOMER_ID,
                "content": {"type": "string", "description": "One statement, in the third person."},
                "type": {"type": "string", "enum": MEMORY_TYPES, "default": "fact"},
            },
            ["customer_id", "content"],
        ),
        remember,
        read_only=False,
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
