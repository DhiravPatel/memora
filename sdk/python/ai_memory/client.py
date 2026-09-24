"""Synchronous and asynchronous clients.

Both share one transport implementation, so retry, timeout and error behaviour cannot
drift apart between them. Retries are bounded, jittered and only applied to requests that
are safe to repeat — every write either carries an idempotency key or is deduplicated by
``external_event_id`` on the server.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx

from ai_memory.errors import MemoryAPIError, MemoryConfigError, MemoryTimeoutError
from ai_memory.models import (
    ActionCheck,
    AgentAction,
    AgentProfile,
    AgentRun,
    AgentSession,
    Approval,
    ConditionResult,
    Customer,
    Customer360,
    CustomerChanges,
    CustomerContext,
    EventExplanation,
    Goal,
    Health,
    LifecycleState,
    Memory,
    QueryResult,
    Recommendation,
    RunExplanation,
    RunTrace,
    SignalReport,
    TrackedEvent,
    TurnResult,
)

DEFAULT_BASE_URL = "https://api.aimemorylayer.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
USER_AGENT = "ai-memory-python/0.1.0"


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else value


def _action_payload(
    customer_id: str,
    action: str,
    request: Mapping[str, Any] | None,
    idempotency_key: str | None,
    approval_id: str | None,
    session_id: str | None,
    agent: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"customer_id": customer_id, "action": action, "request": dict(request or {})}
    for key, value in (
        ("idempotency_key", idempotency_key),
        ("approval_id", approval_id),
        ("session_id", session_id),
        ("agent", agent),
    ):
        if value:
            payload[key] = value
    return payload


def _changes_params(
    since: datetime | str | None,
    until: datetime | str | None,
    agent: str | None,
    types: Sequence[str] | None,
    order: str,
    limit: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {"order": order, "limit": limit}
    if since:
        params["since"] = _iso(since)
    if until:
        params["until"] = _iso(until)
    if agent:
        params["agent"] = agent
    if types:
        params["types"] = ",".join(types)
    return params


def _event_payload(
    *,
    customer_id: str,
    type: str,
    data: Mapping[str, Any] | None = None,
    external_event_id: str | None = None,
    occurred_at: datetime | str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
    source: str = "python-sdk",
) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "event_type": type,
        "data": dict(data or {}),
        "external_event_id": external_event_id,
        "occurred_at": _iso(occurred_at),
        "customer_email": customer_email,
        "customer_name": customer_name,
        "source": source,
    }


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
        error = payload.get("error", {})
    except ValueError:
        error = {}
    raise MemoryAPIError(
        error.get("message") or f"Request failed with status {response.status_code}",
        status=response.status_code,
        code=error.get("code", "error"),
        details=error.get("details"),
        request_id=response.headers.get("x-request-id"),
    )


def _compact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop unset optional fields so the server applies its own defaults."""
    return {key: value for key, value in payload.items() if value is not None}


def _check_payload(
    customer_id: str,
    action: str,
    request: Mapping[str, Any] | None,
    *,
    approval_id: str | None,
    session_id: str | None,
    agent: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    return _compact(
        {
            "customer_id": customer_id,
            "action": action,
            "request": dict(request or {}),
            "approval_id": approval_id,
            "session_id": session_id,
            "agent": agent,
            "dry_run": dry_run or None,
        }
    )


def _profile_payload(**fields: Any) -> dict[str, Any]:
    return {key: (list(value) if isinstance(value, (tuple, set)) else value) for key, value in fields.items() if value is not None}


def _backoff(attempt: int) -> float:
    return min(4.0, 0.25 * (2**attempt)) * (0.5 + random.random() / 2)


class _BaseClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        headers: Mapping[str, str] | None = None,
        transport: Any = None,
        agent_name: str | None = None,
    ) -> None:
        if not api_key:
            raise MemoryConfigError("api_key is required.")
        # An explicit transport lets callers mount the SDK directly onto an ASGI app in
        # tests, or route through a custom proxy in production.
        self.transport = transport
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._extra_headers = dict(headers or {})
        # Labels this client's runs and checks. A key bound to an agent profile is
        # labelled by the profile instead, whatever this says.
        if agent_name:
            self._extra_headers["X-Agent-Name"] = agent_name

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            **self._extra_headers,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers


class MemoryClient(_BaseClient):
    """Synchronous client.

    ```python
    memory = MemoryClient(api_key=os.environ["MEMORY_API_KEY"])
    memory.track(customer_id="cus_123", type="feature_used", data={"feature": "reports"})
    print(memory.query("cus_123", "What problems has this customer had?").answer)
    ```
    """

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._client = httpx.Client(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        )

    # ------------------------------------------------------------- transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    json=json,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    headers=self._headers(idempotency_key),
                )
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                    last_error = MemoryAPIError(
                        "retryable", status=response.status_code
                    )
                    time.sleep(_backoff(attempt))
                    continue
                _raise_for_status(response)
                return response.json() if response.content else None
            except httpx.TimeoutException as exc:
                last_error = MemoryTimeoutError(f"Request timed out after {self.timeout}s")
                if attempt >= self.max_retries:
                    raise last_error from exc
                time.sleep(_backoff(attempt))
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(_backoff(attempt))
        raise last_error if last_error else RuntimeError("unreachable")

    # ---------------------------------------------------------------- events

    def track(self, **kwargs: Any) -> TrackedEvent:
        """Send one event. Pass ``external_event_id`` to make it idempotent."""
        payload = _event_payload(**kwargs)
        return TrackedEvent.from_api(
            self._request(
                "POST", "/v1/events", json=payload,
                idempotency_key=payload.get("external_event_id"),
            )
        )

    def preview(
        self,
        *,
        customer_id: str,
        type: str,
        data: Mapping[str, Any] | None = None,
        occurred_at: datetime | str | None = None,
    ) -> EventExplanation:
        """What this event would do, without sending it.

        The real pipeline, stopped before it writes — so what it reports is what would
        happen, not an approximation of it. Useful in a test, in a migration script before
        committing to a mapping, and in the five minutes after "why did nothing happen?"

            preview = client.preview(customer_id="cus_1", type="support_message",
                                     data={"message": "..."})
            if not preview:
                print(preview.stop_reason or preview.summary)
        """
        return EventExplanation.from_api(
            self._request(
                "POST",
                "/v1/events/preview",
                json={
                    "customer_id": customer_id,
                    "event_type": type,
                    "data": dict(data or {}),
                    "occurred_at": _iso(occurred_at),
                },
            )
        )

    def track_batch(self, events: Iterable[Mapping[str, Any]]) -> list[TrackedEvent]:
        payload = {"events": [_event_payload(**dict(event)) for event in events]}
        body = self._request("POST", "/v1/events/batch", json=payload)
        return [TrackedEvent.from_api(item) for item in body.get("accepted", [])]

    def retry_event(self, event_id: str) -> TrackedEvent:
        return TrackedEvent.from_api(self._request("POST", f"/v1/events/{event_id}/retry"))

    def upsert_customers(self, customers: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Create or update up to 500 customers in one request."""
        return self._request(
            "POST",
            "/v1/customers/batch",
            json={"customers": [dict(customer) for customer in customers]},
        )

    def merge_customers(self, source_customer_id: str, target_customer_id: str) -> dict[str, Any]:
        """Merge a duplicate into the surviving record. Everything derived moves across."""
        return self._request(
            "POST",
            f"/v1/customers/{source_customer_id}/merge",
            json={"into": target_customer_id},
        )

    # ------------------------------------------------------------- customers

    def upsert_customer(
        self,
        external_id: str,
        *,
        email: str | None = None,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Customer:
        return Customer.from_api(
            self._request(
                "POST",
                "/v1/customers",
                json=_compact(
                    {
                        "external_id": external_id,
                        "email": email,
                        "name": name,
                        "metadata": dict(metadata or {}),
                    }
                ),
            )
        )

    def get_customer(self, customer_id: str) -> Customer:
        return Customer.from_api(self._request("GET", f"/v1/customers/{customer_id}"))

    # ------------------------------------------------ quality and evaluation

    def quality(self, *, days: int = 30) -> dict[str, Any]:
        """The quality report: a score, its parts, and a diagnostic with a fix for every
        part that is off."""
        return self._request("GET", "/v1/quality", params={"days": days})

    def eval_sets(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/evals")

    def create_eval_set(self, name: str, *, description: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/v1/evals", json={"name": name, "description": description})

    def add_eval_cases(self, set_id: str, cases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Add cases whose right answers you know — questions retrieval should answer, and
        (``"kind": "extraction"``) events that should become the right memories.

            client.add_eval_cases(set_id, [
                {"customer_id": "cus_1", "question": "Is the integration broken?",
                 "expected_phrases": ["connector"]},
                {"customer_id": "cus_1", "kind": "extraction",
                 "event": {"event_type": "support_message", "data": {"message": "The sync fails."}},
                 "expect": [{"type": "problem", "contains": "sync fail"}],
                 "forbid": [{"type": "intent"}]},
            ])
        """
        return self._request("POST", f"/v1/evals/{set_id}/cases", json={"cases": [dict(case) for case in cases]})

    def eval_regression(self, set_id: str, settings: Mapping[str, Any], *, k: int = 10) -> dict[str, Any]:
        """Before changing a setting: the set as configured and under ``settings`` (validated
        like a save, never saved). ``safe`` is false when a passing case would fail, and
        ``newly_failing`` names them."""
        return self._request("POST", f"/v1/evals/{set_id}/regression", json={"settings": dict(settings), "k": k})

    def eval_scorecard(self) -> dict[str, Any]:
        """Retrieval recall, MRR and citation accuracy; extraction accuracy, false-memory
        rate and type, sensitivity and consolidation accuracy; duplicate control and
        consistency — memory quality in one place."""
        return self._request("GET", "/v1/evals/scorecard")

    def run_eval(self, set_id: str, *, label: str | None = None, k: int = 10, wait: bool = True) -> dict[str, Any]:
        """Run a set against retrieval. The result includes the comparison with the
        previous run, and ``comparison["regressed"]`` is true if any question that used to
        be answered no longer is — the flag to fail a CI job on."""
        return self._request(
            "POST", f"/v1/evals/{set_id}/runs", json={"label": label, "k": k, "wait": wait}
        )

    def eval_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/evals/runs/{run_id}")

    # ---------------------------------------------- facts, conditions, lifecycle

    def facts(self, customer_id: str) -> dict[str, Any]:
        """Every fact a rule can read about the customer, with the evidence behind each."""
        return self._request("GET", f"/v1/customers/{customer_id}/facts")

    def fact_catalog(self) -> dict[str, Any]:
        return self._request("GET", "/v1/conditions/catalog")

    def validate_condition(self, condition: str | dict[str, Any]) -> dict[str, Any]:
        """Check a condition without evaluating it. ``valid`` says whether it compiled."""
        return self._request("POST", "/v1/conditions/validate", json={"condition": condition})

    def evaluate_condition(self, customer_id: str, condition: str | dict[str, Any]) -> ConditionResult:
        """Evaluate a condition against a customer.

            if client.evaluate_condition("cus_1", 'problems.entities contains "billing"'):
                hold_the_upsell()
        """
        return ConditionResult.from_api(
            self._request(
                "POST",
                "/v1/conditions/evaluate",
                json={"customer_id": customer_id, "condition": condition},
            )
        )

    def lifecycle_state(self, customer_id: str, *, track: str = "lifecycle") -> LifecycleState | None:
        """The customer's current state on a track, or ``None`` if not placed yet."""
        return self.lifecycle_states(customer_id).get(track)

    def lifecycle_states(self, customer_id: str) -> dict[str, LifecycleState | None]:
        """The customer's current state on every track, keyed by track name."""
        body = self._request("GET", f"/v1/customers/{customer_id}/state")
        tracks = body.get("tracks") or [{"track": "lifecycle", "current": body.get("current")}]
        return {
            item["track"]: LifecycleState.from_api(item["current"]) if item.get("current") else None
            for item in tracks
        }

    def lifecycle_history(
        self, customer_id: str, *, track: str = "lifecycle", limit: int = 50
    ) -> list[LifecycleState]:
        """``track="all"`` interleaves every track by time."""
        body = self._request(
            "GET", f"/v1/customers/{customer_id}/state/history", params={"limit": limit, "track": track}
        )
        return [LifecycleState.from_api(item) for item in body.get("data", [])]

    def set_lifecycle_state(
        self,
        customer_id: str,
        state: str,
        *,
        track: str = "lifecycle",
        pin: bool = True,
        pin_days: int | None = None,
        note: str | None = None,
    ) -> LifecycleState:
        """Set the state on a track by hand. Pinned by default, so the machine leaves it alone."""
        return LifecycleState.from_api(
            self._request(
                "PUT",
                f"/v1/customers/{customer_id}/state",
                json={"state": state, "track": track, "pin": pin, "pin_days": pin_days, "note": note},
            )
        )

    def release_lifecycle_state(self, customer_id: str, *, track: str = "lifecycle") -> LifecycleState:
        return LifecycleState.from_api(
            self._request("DELETE", f"/v1/customers/{customer_id}/state/pin", params={"track": track})
        )

    def refresh_lifecycle_state(self, customer_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/customers/{customer_id}/state/refresh")

    def lifecycle(self) -> dict[str, Any]:
        """The project's machine and how many customers are in each state."""
        return self._request("GET", "/v1/lifecycle")

    def lifecycle_templates(self) -> list[dict[str, Any]]:
        """The shipped tracks (engagement, commercial), ready for `lifecycle_tracks`."""
        return self._request("GET", "/v1/lifecycle/templates")

    def customers_in_state(
        self, state: str, *, track: str = "lifecycle", limit: int = 50, offset: int = 0
    ) -> list[Customer]:
        body = self._request(
            "GET",
            "/v1/lifecycle/customers",
            params={"state": state, "track": track, "limit": limit, "offset": offset},
        )
        return [Customer.from_api(item) for item in body.get("data", [])]

    def snapshots(
        self,
        customer_id: str,
        *,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Every material change in what was known about the customer, newest first."""
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = _iso(since)
        if until:
            params["until"] = _iso(until)
        return self._request("GET", f"/v1/customers/{customer_id}/snapshots", params=params).get("data", [])

    def snapshot_at(self, customer_id: str, time: datetime | str) -> dict[str, Any]:
        """What was known about the customer at a moment in the past."""
        return self._request("GET", f"/v1/customers/{customer_id}/snapshots/at", params={"time": _iso(time)})

    def changes(
        self,
        customer_id: str,
        *,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        agent: str | None = None,
        types: Sequence[str] | None = None,
        order: str = "time",
        limit: int = 50,
    ) -> CustomerChanges:
        """What changed about the customer since a moment, and what they looked like then and now.

        ``since`` is a span ("7d", "12h", "2w", "3mo"), a time, a snapshot id, or
        ``"last_session"`` — "since I last spoke to them" — or ``"last_run"``. Pass ``agent``
        with either to mean that agent's last conversation or action.

            brief = client.changes("cus_1", since="last_session", agent="support-bot")
            print(brief.summary)  # "Since the last conversation (17 Sep 2026): upgraded to Pro; …"
        """
        return CustomerChanges.from_api(
            self._request("GET", f"/v1/customers/{customer_id}/changes", params=_changes_params(since, until, agent, types, order, limit))
        )

    def compare(
        self, customer_id: str, start: datetime | str, end: datetime | str | None = None
    ) -> dict[str, Any]:
        """The customer at two moments side by side ("then vs now"), with every fact that differs."""
        params = {"from": _iso(start), **({"to": _iso(end)} if end else {})}
        return self._request("GET", f"/v1/customers/{customer_id}/compare", params=params)

    def customer_360(
        self, customer_id: str, *, include: Sequence[str] | None = None
    ) -> Customer360:
        """Everything worth knowing about a customer, in one call.

        What an agent reads before it replies: health, current plan, open problems, stated
        preferences and goals, where the customer is heading, what to do about it, and what
        was said last time. Pass ``include`` to build only some of it — the response goes
        into somebody's context window.

            view = client.customer_360("cus_1", include=["health", "active_problems"])
            if view.is_at_risk:
                escalate(view.summary)
        """
        params = {"include": ",".join(include)} if include else None
        return Customer360.from_api(
            self._request("GET", f"/v1/customers/{customer_id}/360", params=params)
        )

    def memories(self, customer_id: str, *, type: str | None = None, limit: int = 50) -> list[Memory]:
        body = self._request(
            "GET", f"/v1/customers/{customer_id}/memories", params={"type": type, "limit": limit}
        )
        return [Memory.from_api(item) for item in body.get("data", [])]

    def timeline(self, customer_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        body = self._request(
            "GET", f"/v1/customers/{customer_id}/timeline", params={"limit": limit}
        )
        return list(body.get("entries", []))

    def health(self, customer_id: str) -> Health:
        return Health.from_api(self._request("GET", f"/v1/customers/{customer_id}/health"))

    def links(self, customer_id: str, *, link_type: str | None = None) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/customers/{customer_id}/links", params={"link_type": link_type}
        )

    def export(self, customer_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/customers/{customer_id}/export")

    def delete_customer(self, customer_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/customers/{customer_id}")

    # ---------------------------------------------------------------- memory

    def query(
        self,
        customer_id: str,
        question: str,
        *,
        limit: int = 10,
        include_trace: bool = False,
        session_id: str | None = None,
    ) -> QueryResult:
        return QueryResult.from_api(
            self._request(
                "POST",
                "/v1/memory/query",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "query": question,
                        "limit": limit,
                        "include_trace": include_trace,
                        "session_id": session_id,
                    }
                ),
            )
        )

    def search(
        self,
        question: str,
        *,
        customer_id: str | None = None,
        limit: int = 10,
        types: list[str] | None = None,
    ) -> list[Memory]:
        body = self._request(
            "POST",
            "/v1/memory/search",
            json={"query": question, "customer_id": customer_id, "limit": limit, "types": types},
        )
        return [Memory.from_api(item) for item in body.get("memories", [])]

    def context(
        self,
        customer_id: str,
        *,
        task: str | None = None,
        query: str | None = None,
        token_budget: int | None = None,
        as_text: bool = False,
        session_id: str | None = None,
    ) -> CustomerContext:
        return CustomerContext.from_api(
            self._request(
                "POST",
                "/v1/memory/context",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "task": task,
                        "query": query,
                        "token_budget": token_budget,
                        "format": "text" if as_text else "json",
                        "session_id": session_id,
                    }
                ),
            )
        )

    def remember(
        self,
        customer_id: str,
        content: str,
        *,
        type: str = "fact",
        importance: float | None = None,
        confidence: float | None = None,
    ) -> Memory:
        return Memory.from_api(
            self._request(
                "POST",
                "/v1/memories",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "content": content,
                        "type": type,
                        "importance": importance,
                        "confidence": confidence,
                    }
                ),
            )
        )

    def feedback(
        self, memory_id: str, verdict: str, *, content: str | None = None, note: str | None = None
    ) -> dict[str, Any]:
        """Confirm, reject or correct a memory."""
        return self._request(
            "POST",
            f"/v1/memories/{memory_id}/feedback",
            json=_compact({"verdict": verdict, "content": content, "note": note}),
        )

    # ------------------------------------------------------------- foresight

    def signals(self, customer_id: str, *, series: bool = True) -> SignalReport:
        """Where this customer is heading, and the observations that say so."""
        return SignalReport.from_api(
            self._request(
                "GET", f"/v1/customers/{customer_id}/signals", params={"series": series}
            )
        )

    def recommendations(self, customer_id: str) -> list[Recommendation]:
        """What to do about this customer next, most urgent first."""
        body = self._request("GET", f"/v1/customers/{customer_id}/recommendations")
        return [Recommendation.from_api(item) for item in body.get("recommendations", [])]

    def goals(self, customer_id: str, *, status: str | None = None, limit: int = 50) -> list[Goal]:
        """What this customer said they were trying to do, and how far it got."""
        body = self._request(
            "GET",
            f"/v1/customers/{customer_id}/goals",
            params={"status": status, "limit": limit},
        )
        return [Goal.from_api(item) for item in body.get("data", [])]

    def all_goals(self, *, status: str | None = None, limit: int = 50) -> list[Goal]:
        body = self._request("GET", "/v1/goals", params={"status": status, "limit": limit})
        return [Goal.from_api(item) for item in body.get("data", [])]

    def set_goal_status(self, goal_id: str, status: str, *, note: str | None = None) -> Goal:
        """Record a person's verdict. The tracker then leaves this goal alone."""
        return Goal.from_api(
            self._request(
                "PATCH", f"/v1/goals/{goal_id}", json=_compact({"status": status, "note": note})
            )
        )

    # --------------------------------------------------------- agent sessions

    def open_session(
        self,
        customer_id: str,
        *,
        agent: str = "agent",
        external_id: str | None = None,
        channel: str | None = None,
        token_budget: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AgentSession:
        """Open (or resume) a session and get briefed on the customer.

        Passing your own conversation id as ``external_id`` makes this idempotent, so a
        reconnect resumes rather than starting a second session.
        """
        return AgentSession.from_api(
            self._request(
                "POST",
                "/v1/agent/sessions",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "agent": agent,
                        "external_id": external_id,
                        "channel": channel,
                        "token_budget": token_budget,
                        "metadata": dict(metadata) if metadata else None,
                    }
                ),
            )
        )

    def add_turn(
        self,
        session_id: str,
        content: str,
        *,
        role: str = "user",
        remember: bool | None = None,
        retrieve: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> TurnResult:
        """Record a turn. Customer turns become memory; agent turns are recorded only."""
        return TurnResult.from_api(
            self._request(
                "POST",
                f"/v1/agent/sessions/{session_id}/turns",
                json=_compact(
                    {
                        "role": role,
                        "content": content,
                        "remember": remember,
                        "retrieve": retrieve,
                        "metadata": dict(metadata) if metadata else None,
                    }
                ),
            )
        )

    def close_session(
        self, session_id: str, *, outcome: str | None = None, write_summary: bool = True
    ) -> AgentSession:
        """Close the session, writing what it established into long-term memory."""
        body = self._request(
            "POST",
            f"/v1/agent/sessions/{session_id}/close",
            json=_compact({"write_summary": write_summary, "outcome": outcome}),
        )
        return AgentSession.from_api(body["session"])

    def get_session(self, session_id: str) -> AgentSession:
        return AgentSession.from_api(self._request("GET", f"/v1/agent/sessions/{session_id}"))

    def sessions(
        self, *, customer_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[AgentSession]:
        body = self._request(
            "GET",
            "/v1/agent/sessions",
            params={"customer_id": customer_id, "status": status, "limit": limit},
        )
        return [AgentSession.from_api(item) for item in body.get("data", [])]


    # ---------------------------------------------------------------- agents

    def check_action(
        self,
        customer_id: str,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        approval_id: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
        dry_run: bool = False,
    ) -> ActionCheck:
        """Ask before acting: may this agent do ``action`` to this customer, now?

        ``request`` carries the details your rules read — ``channel``, ``amount``,
        ``topic``, ``plan`` or anything else. A ``require_approval`` result files a request
        for a person (``check.approval``); once approved, call again with ``approval_id``.
        """
        return ActionCheck.from_api(
            self._request(
                "POST",
                "/v1/agent/check",
                json=_check_payload(
                    customer_id, action, request,
                    approval_id=approval_id, session_id=session_id, agent=agent, dry_run=dry_run,
                ),
            )
        )

    def approval(self, approval_id: str) -> Approval:
        return Approval.from_api(self._request("GET", f"/v1/agent/approvals/{approval_id}"))

    def approvals(
        self, *, status: str | None = None, customer_id: str | None = None, limit: int = 50
    ) -> list[Approval]:
        body = self._request(
            "GET", "/v1/agent/approvals", params={"status": status, "customer_id": customer_id, "limit": limit}
        )
        return [Approval.from_api(item) for item in body.get("data", [])]

    def decide_approval(self, approval_id: str, decision: str, *, note: str | None = None) -> Approval:
        """``decision`` is ``approve`` or ``reject``. Needs the ``approvals:decide`` scope."""
        return Approval.from_api(
            self._request(
                "POST",
                f"/v1/agent/approvals/{approval_id}/decision",
                json=_compact({"decision": decision, "note": note}),
            )
        )

    def request_action(
        self,
        customer_id: str,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        approval_id: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> AgentAction:
        """The one call before acting (§26 4.5): decided like :meth:`check_action`, recorded
        as an action. ``allowed`` — go ahead and :meth:`complete_action`; ``pending_approval``
        — a person was asked, :meth:`proceed_action` once they decide; ``denied`` — don't.

            action = client.request_action("cus_1", "process_refund", {"amount": 25}, idempotency_key=ticket_id)
            if action.allowed:
                billing.refund(25)
                client.complete_action(action.id, "done", external_ref=refund_id)
        """
        return AgentAction.from_api(
            self._request(
                "POST",
                "/v1/agent/actions/request",
                json=_action_payload(customer_id, action, request, idempotency_key, approval_id, session_id, agent),
            )
        )

    def action(self, action_id: str) -> AgentAction:
        return AgentAction.from_api(self._request("GET", f"/v1/agent/actions/{action_id}"))

    def proceed_action(self, action_id: str) -> AgentAction:
        """Go ahead with an action a person approved — the rules run again on today's facts."""
        return AgentAction.from_api(self._request("POST", f"/v1/agent/actions/{action_id}/proceed"))

    def complete_action(
        self, action_id: str, outcome: str = "done", *, note: str | None = None, external_ref: str | None = None
    ) -> AgentAction:
        """Report what happened: ``done``, ``failed`` or ``cancelled``."""
        return AgentAction.from_api(
            self._request(
                "POST",
                f"/v1/agent/actions/{action_id}/complete",
                json={"outcome": outcome, "note": note, "external_ref": external_ref},
            )
        )

    def actions(
        self,
        *,
        customer_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        limit: int = 50,
    ) -> list[AgentAction]:
        params = {key: value for key, value in (("customer_id", customer_id), ("action", action), ("status", status), ("agent", agent)) if value}
        body = self._request("GET", "/v1/agent/actions", params={**params, "limit": limit})
        return [AgentAction.from_api(item) for item in body.get("data", [])]

    def wait_for_action(self, action_id: str, *, timeout: float = 300.0, interval: float = 5.0) -> AgentAction:
        """Wait for a person to decide on a waiting action, then proceed with it; returns it
        whatever happened — allowed, denied, lapsed, or still waiting at the timeout."""
        deadline = time.monotonic() + timeout
        while True:
            current = self.action(action_id)
            if not current.waiting:
                return current
            if current.approval is not None and not current.approval.is_pending:
                return self.proceed_action(action_id)
            if time.monotonic() >= deadline:
                return current
            time.sleep(max(0.5, interval))

    def wait_for_approval(
        self, approval_id: str, *, timeout: float = 300.0, interval: float = 5.0
    ) -> Approval:
        """Poll until a person decides or the request lapses; returns it either way.

        For anything longer than a conversation, subscribe to ``agent.approval_decided``
        instead of holding a worker open.
        """
        deadline = time.monotonic() + timeout
        while True:
            current = self.approval(approval_id)
            if not current.is_pending or time.monotonic() >= deadline:
                return current
            time.sleep(max(0.5, interval))

    def checks(
        self,
        *,
        customer_id: str | None = None,
        decision: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionCheck]:
        body = self._request(
            "GET",
            "/v1/agent/checks",
            params={
                "customer_id": customer_id,
                "decision": decision,
                "agent": agent,
                "session_id": session_id,
                "limit": limit,
            },
        )
        return [ActionCheck.from_api(item) for item in body.get("data", [])]

    def runs(
        self,
        *,
        customer_id: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        body = self._request(
            "GET",
            "/v1/agent/runs",
            params={"customer_id": customer_id, "agent": agent, "session_id": session_id, "kind": kind, "limit": limit},
        )
        return [AgentRun.from_api(item) for item in body.get("data", [])]

    def run(self, run_id: str) -> AgentRun:
        return AgentRun.from_api(self._request("GET", f"/v1/agent/runs/{run_id}"))

    def explain_run(self, run_id: str) -> RunExplanation:
        """Why did the agent say that? Pass the ``run_id`` from a query or context call."""
        return RunExplanation.from_api(self._request("GET", f"/v1/agent/runs/{run_id}/explain"))

    def run_trace(self, run_id: str) -> RunTrace:
        """Why did my agent do this? What it was given, what it was not given and why, and
        what it decided — with the guardrail checks around it."""
        return RunTrace.from_api(self._request("GET", f"/v1/agent/runs/{run_id}/trace"))

    def my_profile(self) -> AgentProfile | None:
        """The agent profile this key acts as, or ``None`` for an unbound key."""
        body = self._request("GET", "/v1/agent/profiles/me")
        return AgentProfile.from_api(body) if body else None

    def agent_profiles(self) -> list[AgentProfile]:
        return [AgentProfile.from_api(item) for item in self._request("GET", "/v1/agent/profiles")]

    def create_agent_profile(
        self,
        name: str,
        *,
        description: str | None = None,
        readable_types: Sequence[str] | None = None,
        can_read_restricted: bool = False,
        allowed_actions: Sequence[str] | None = None,
        denied_actions: Sequence[str] | None = None,
    ) -> AgentProfile:
        """Needs an ``admin`` key. Bind keys to it from the dashboard or the admin API."""
        return AgentProfile.from_api(
            self._request(
                "POST",
                "/v1/agent/profiles",
                json=_profile_payload(
                    name=name,
                    description=description,
                    readable_types=readable_types,
                    can_read_restricted=can_read_restricted,
                    allowed_actions=allowed_actions,
                    denied_actions=denied_actions,
                ),
            )
        )

    def update_agent_profile(self, profile_id: str, **changes: Any) -> AgentProfile:
        return AgentProfile.from_api(
            self._request("PATCH", f"/v1/agent/profiles/{profile_id}", json=_profile_payload(**changes))
        )

    def delete_agent_profile(self, profile_id: str) -> None:
        self._request("DELETE", f"/v1/agent/profiles/{profile_id}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MemoryClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncMemoryClient(_BaseClient):
    """Asynchronous client with the same surface as :class:`MemoryClient`."""

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        import asyncio

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    headers=self._headers(idempotency_key),
                )
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                _raise_for_status(response)
                return response.json() if response.content else None
            except httpx.TimeoutException as exc:
                last_error = MemoryTimeoutError(f"Request timed out after {self.timeout}s")
                if attempt >= self.max_retries:
                    raise last_error from exc
                await asyncio.sleep(_backoff(attempt))
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(_backoff(attempt))
        raise last_error if last_error else RuntimeError("unreachable")

    async def preview(
        self,
        *,
        customer_id: str,
        type: str,
        data: Mapping[str, Any] | None = None,
        occurred_at: datetime | str | None = None,
    ) -> EventExplanation:
        """What this event would do, without sending it.

        The real pipeline, stopped before it writes — so what it reports is what would
        happen, not an approximation of it. Useful in a test, in a migration script before
        committing to a mapping, and in the five minutes after "why did nothing happen?"

            preview = client.preview(customer_id="cus_1", type="support_message",
                                     data={"message": "..."})
            if not preview:
                print(preview.stop_reason or preview.summary)
        """
        return EventExplanation.from_api(
            await self._request(
                "POST",
                "/v1/events/preview",
                json={
                    "customer_id": customer_id,
                    "event_type": type,
                    "data": dict(data or {}),
                    "occurred_at": _iso(occurred_at),
                },
            )
        )

    # ------------------------------------------------ quality and evaluation

    async def quality(self, *, days: int = 30) -> dict[str, Any]:
        """The quality report: a score, its parts, and a diagnostic with a fix for every
        part that is off."""
        return await self._request("GET", "/v1/quality", params={"days": days})

    async def eval_sets(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/evals")

    async def create_eval_set(self, name: str, *, description: str | None = None) -> dict[str, Any]:
        return await self._request("POST", "/v1/evals", json={"name": name, "description": description})

    async def add_eval_cases(self, set_id: str, cases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Add questions whose right answers you know.

            client.add_eval_cases(set_id, [
                {"customer_id": "cus_1", "question": "Is the integration broken?",
                 "expected_phrases": ["connector"]},
            ])
        """
        return await self._request("POST", f"/v1/evals/{set_id}/cases", json={"cases": [dict(case) for case in cases]})

    async def eval_regression(self, set_id: str, settings: Mapping[str, Any], *, k: int = 10) -> dict[str, Any]:
        return await self._request("POST", f"/v1/evals/{set_id}/regression", json={"settings": dict(settings), "k": k})

    async def eval_scorecard(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/evals/scorecard")

    async def run_eval(self, set_id: str, *, label: str | None = None, k: int = 10, wait: bool = True) -> dict[str, Any]:
        """Run a set against retrieval. The result includes the comparison with the
        previous run, and ``comparison["regressed"]`` is true if any question that used to
        be answered no longer is — the flag to fail a CI job on."""
        return await self._request(
            "POST", f"/v1/evals/{set_id}/runs", json={"label": label, "k": k, "wait": wait}
        )

    async def eval_run(self, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/evals/runs/{run_id}")

    # ---------------------------------------------- facts, conditions, lifecycle

    async def facts(self, customer_id: str) -> dict[str, Any]:
        """Every fact a rule can read about the customer, with the evidence behind each."""
        return await self._request("GET", f"/v1/customers/{customer_id}/facts")

    async def fact_catalog(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/conditions/catalog")

    async def validate_condition(self, condition: str | dict[str, Any]) -> dict[str, Any]:
        """Check a condition without evaluating it. ``valid`` says whether it compiled."""
        return await self._request("POST", "/v1/conditions/validate", json={"condition": condition})

    async def evaluate_condition(self, customer_id: str, condition: str | dict[str, Any]) -> ConditionResult:
        """Evaluate a condition against a customer.

            if client.evaluate_condition("cus_1", 'problems.entities contains "billing"'):
                hold_the_upsell()
        """
        return ConditionResult.from_api(
            await self._request(
                "POST",
                "/v1/conditions/evaluate",
                json={"customer_id": customer_id, "condition": condition},
            )
        )

    async def lifecycle_state(self, customer_id: str, *, track: str = "lifecycle") -> LifecycleState | None:
        return (await self.lifecycle_states(customer_id)).get(track)

    async def lifecycle_states(self, customer_id: str) -> dict[str, LifecycleState | None]:
        body = await self._request("GET", f"/v1/customers/{customer_id}/state")
        tracks = body.get("tracks") or [{"track": "lifecycle", "current": body.get("current")}]
        return {
            item["track"]: LifecycleState.from_api(item["current"]) if item.get("current") else None
            for item in tracks
        }

    async def lifecycle_history(
        self, customer_id: str, *, track: str = "lifecycle", limit: int = 50
    ) -> list[LifecycleState]:
        body = await self._request(
            "GET", f"/v1/customers/{customer_id}/state/history", params={"limit": limit, "track": track}
        )
        return [LifecycleState.from_api(item) for item in body.get("data", [])]

    async def set_lifecycle_state(
        self,
        customer_id: str,
        state: str,
        *,
        track: str = "lifecycle",
        pin: bool = True,
        pin_days: int | None = None,
        note: str | None = None,
    ) -> LifecycleState:
        return LifecycleState.from_api(
            await self._request(
                "PUT",
                f"/v1/customers/{customer_id}/state",
                json={"state": state, "track": track, "pin": pin, "pin_days": pin_days, "note": note},
            )
        )

    async def release_lifecycle_state(self, customer_id: str, *, track: str = "lifecycle") -> LifecycleState:
        return LifecycleState.from_api(
            await self._request("DELETE", f"/v1/customers/{customer_id}/state/pin", params={"track": track})
        )

    async def refresh_lifecycle_state(self, customer_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v1/customers/{customer_id}/state/refresh")

    async def lifecycle(self) -> dict[str, Any]:
        """The project's machine and how many customers are in each state."""
        return await self._request("GET", "/v1/lifecycle")

    async def customers_in_state(
        self, state: str, *, track: str = "lifecycle", limit: int = 50, offset: int = 0
    ) -> list[Customer]:
        body = await self._request(
            "GET",
            "/v1/lifecycle/customers",
            params={"state": state, "track": track, "limit": limit, "offset": offset},
        )
        return [Customer.from_api(item) for item in body.get("data", [])]

    async def snapshots(
        self,
        customer_id: str,
        *,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Every material change in what was known about the customer, newest first."""
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = _iso(since)
        if until:
            params["until"] = _iso(until)
        return (await self._request("GET", f"/v1/customers/{customer_id}/snapshots", params=params)).get(
            "data", []
        )

    async def snapshot_at(self, customer_id: str, time: datetime | str) -> dict[str, Any]:
        """What was known about the customer at a moment in the past."""
        return await self._request("GET", f"/v1/customers/{customer_id}/snapshots/at", params={"time": _iso(time)})

    async def changes(
        self,
        customer_id: str,
        *,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        agent: str | None = None,
        types: Sequence[str] | None = None,
        order: str = "time",
        limit: int = 50,
    ) -> CustomerChanges:
        """What changed about the customer since a moment — see :meth:`MemoryClient.changes`."""
        return CustomerChanges.from_api(
            await self._request(
                "GET", f"/v1/customers/{customer_id}/changes", params=_changes_params(since, until, agent, types, order, limit)
            )
        )

    async def compare(
        self, customer_id: str, start: datetime | str, end: datetime | str | None = None
    ) -> dict[str, Any]:
        """The customer at two moments side by side, with every fact that differs."""
        params = {"from": _iso(start), **({"to": _iso(end)} if end else {})}
        return await self._request("GET", f"/v1/customers/{customer_id}/compare", params=params)

    async def customer_360(
        self, customer_id: str, *, include: Sequence[str] | None = None
    ) -> Customer360:
        """Everything worth knowing about a customer, in one call.

        What an agent reads before it replies: health, current plan, open problems, stated
        preferences and goals, where the customer is heading, what to do about it, and what
        was said last time. Pass ``include`` to build only some of it — the response goes
        into somebody's context window.

            view = client.customer_360("cus_1", include=["health", "active_problems"])
            if view.is_at_risk:
                escalate(view.summary)
        """
        params = {"include": ",".join(include)} if include else None
        return Customer360.from_api(
            await self._request("GET", f"/v1/customers/{customer_id}/360", params=params)
        )

    async def track(self, **kwargs: Any) -> TrackedEvent:
        payload = _event_payload(**kwargs)
        return TrackedEvent.from_api(
            await self._request(
                "POST", "/v1/events", json=payload,
                idempotency_key=payload.get("external_event_id"),
            )
        )

    async def query(
        self,
        customer_id: str,
        question: str,
        *,
        limit: int = 10,
        include_trace: bool = False,
        session_id: str | None = None,
    ) -> QueryResult:
        return QueryResult.from_api(
            await self._request(
                "POST",
                "/v1/memory/query",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "query": question,
                        "limit": limit,
                        "include_trace": include_trace,
                        "session_id": session_id,
                    }
                ),
            )
        )

    async def context(
        self,
        customer_id: str,
        *,
        task: str | None = None,
        query: str | None = None,
        token_budget: int | None = None,
        as_text: bool = False,
        session_id: str | None = None,
    ) -> CustomerContext:
        return CustomerContext.from_api(
            await self._request(
                "POST",
                "/v1/memory/context",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "task": task,
                        "query": query,
                        "token_budget": token_budget,
                        "format": "text" if as_text else "json",
                        "session_id": session_id,
                    }
                ),
            )
        )

    async def health(self, customer_id: str) -> Health:
        return Health.from_api(await self._request("GET", f"/v1/customers/{customer_id}/health"))

    async def memories(self, customer_id: str, *, limit: int = 50) -> list[Memory]:
        body = await self._request(
            "GET", f"/v1/customers/{customer_id}/memories", params={"limit": limit}
        )
        return [Memory.from_api(item) for item in body.get("data", [])]

    async def signals(self, customer_id: str, *, series: bool = True) -> SignalReport:
        return SignalReport.from_api(
            await self._request(
                "GET", f"/v1/customers/{customer_id}/signals", params={"series": series}
            )
        )

    async def recommendations(self, customer_id: str) -> list[Recommendation]:
        body = await self._request("GET", f"/v1/customers/{customer_id}/recommendations")
        return [Recommendation.from_api(item) for item in body.get("recommendations", [])]

    async def goals(self, customer_id: str, *, status: str | None = None) -> list[Goal]:
        body = await self._request(
            "GET", f"/v1/customers/{customer_id}/goals", params={"status": status}
        )
        return [Goal.from_api(item) for item in body.get("data", [])]

    async def open_session(
        self,
        customer_id: str,
        *,
        agent: str = "agent",
        external_id: str | None = None,
        channel: str | None = None,
        token_budget: int | None = None,
    ) -> AgentSession:
        return AgentSession.from_api(
            await self._request(
                "POST",
                "/v1/agent/sessions",
                json=_compact(
                    {
                        "customer_id": customer_id,
                        "agent": agent,
                        "external_id": external_id,
                        "channel": channel,
                        "token_budget": token_budget,
                    }
                ),
            )
        )

    async def add_turn(
        self,
        session_id: str,
        content: str,
        *,
        role: str = "user",
        remember: bool | None = None,
        retrieve: bool = True,
    ) -> TurnResult:
        return TurnResult.from_api(
            await self._request(
                "POST",
                f"/v1/agent/sessions/{session_id}/turns",
                json=_compact(
                    {
                        "role": role,
                        "content": content,
                        "remember": remember,
                        "retrieve": retrieve,
                    }
                ),
            )
        )

    async def close_session(
        self, session_id: str, *, outcome: str | None = None, write_summary: bool = True
    ) -> AgentSession:
        body = await self._request(
            "POST",
            f"/v1/agent/sessions/{session_id}/close",
            json=_compact({"write_summary": write_summary, "outcome": outcome}),
        )
        return AgentSession.from_api(body["session"])


    # ---------------------------------------------------------------- agents

    async def check_action(
        self,
        customer_id: str,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        approval_id: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
        dry_run: bool = False,
    ) -> ActionCheck:
        return ActionCheck.from_api(
            await self._request(
                "POST",
                "/v1/agent/check",
                json=_check_payload(
                    customer_id, action, request,
                    approval_id=approval_id, session_id=session_id, agent=agent, dry_run=dry_run,
                ),
            )
        )

    async def approval(self, approval_id: str) -> Approval:
        return Approval.from_api(await self._request("GET", f"/v1/agent/approvals/{approval_id}"))

    async def approvals(
        self, *, status: str | None = None, customer_id: str | None = None, limit: int = 50
    ) -> list[Approval]:
        body = await self._request(
            "GET", "/v1/agent/approvals", params={"status": status, "customer_id": customer_id, "limit": limit}
        )
        return [Approval.from_api(item) for item in body.get("data", [])]

    async def decide_approval(self, approval_id: str, decision: str, *, note: str | None = None) -> Approval:
        return Approval.from_api(
            await self._request(
                "POST",
                f"/v1/agent/approvals/{approval_id}/decision",
                json=_compact({"decision": decision, "note": note}),
            )
        )

    async def request_action(
        self,
        customer_id: str,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        approval_id: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> AgentAction:
        return AgentAction.from_api(
            await self._request(
                "POST",
                "/v1/agent/actions/request",
                json=_action_payload(customer_id, action, request, idempotency_key, approval_id, session_id, agent),
            )
        )

    async def action(self, action_id: str) -> AgentAction:
        return AgentAction.from_api(await self._request("GET", f"/v1/agent/actions/{action_id}"))

    async def proceed_action(self, action_id: str) -> AgentAction:
        return AgentAction.from_api(await self._request("POST", f"/v1/agent/actions/{action_id}/proceed"))

    async def complete_action(
        self, action_id: str, outcome: str = "done", *, note: str | None = None, external_ref: str | None = None
    ) -> AgentAction:
        return AgentAction.from_api(
            await self._request(
                "POST",
                f"/v1/agent/actions/{action_id}/complete",
                json={"outcome": outcome, "note": note, "external_ref": external_ref},
            )
        )

    async def actions(
        self,
        *,
        customer_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        limit: int = 50,
    ) -> list[AgentAction]:
        params = {key: value for key, value in (("customer_id", customer_id), ("action", action), ("status", status), ("agent", agent)) if value}
        body = await self._request("GET", "/v1/agent/actions", params={**params, "limit": limit})
        return [AgentAction.from_api(item) for item in body.get("data", [])]

    async def wait_for_action(
        self, action_id: str, *, timeout: float = 300.0, interval: float = 5.0  # noqa: ASYNC109 - same signature as the sync client
    ) -> AgentAction:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            current = await self.action(action_id)
            if not current.waiting:
                return current
            if current.approval is not None and not current.approval.is_pending:
                return await self.proceed_action(action_id)
            if asyncio.get_running_loop().time() >= deadline:
                return current
            await asyncio.sleep(max(0.5, interval))

    async def wait_for_approval(
        self, approval_id: str, *, timeout: float = 300.0, interval: float = 5.0  # noqa: ASYNC109 - same signature as the sync client
    ) -> Approval:
        import asyncio

        deadline = time.monotonic() + timeout
        while True:
            current = await self.approval(approval_id)
            if not current.is_pending or time.monotonic() >= deadline:
                return current
            await asyncio.sleep(max(0.5, interval))

    async def checks(
        self,
        *,
        customer_id: str | None = None,
        decision: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionCheck]:
        body = await self._request(
            "GET",
            "/v1/agent/checks",
            params={
                "customer_id": customer_id,
                "decision": decision,
                "agent": agent,
                "session_id": session_id,
                "limit": limit,
            },
        )
        return [ActionCheck.from_api(item) for item in body.get("data", [])]

    async def runs(
        self,
        *,
        customer_id: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        body = await self._request(
            "GET",
            "/v1/agent/runs",
            params={"customer_id": customer_id, "agent": agent, "session_id": session_id, "kind": kind, "limit": limit},
        )
        return [AgentRun.from_api(item) for item in body.get("data", [])]

    async def run(self, run_id: str) -> AgentRun:
        return AgentRun.from_api(await self._request("GET", f"/v1/agent/runs/{run_id}"))

    async def explain_run(self, run_id: str) -> RunExplanation:
        return RunExplanation.from_api(await self._request("GET", f"/v1/agent/runs/{run_id}/explain"))

    async def run_trace(self, run_id: str) -> RunTrace:
        return RunTrace.from_api(await self._request("GET", f"/v1/agent/runs/{run_id}/trace"))

    async def my_profile(self) -> AgentProfile | None:
        body = await self._request("GET", "/v1/agent/profiles/me")
        return AgentProfile.from_api(body) if body else None

    async def agent_profiles(self) -> list[AgentProfile]:
        return [AgentProfile.from_api(item) for item in await self._request("GET", "/v1/agent/profiles")]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncMemoryClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
