"""Synchronous and asynchronous clients.

Both share one transport implementation, so retry, timeout and error behaviour cannot
drift apart between them. Retries are bounded, jittered and only applied to requests that
are safe to repeat — every write either carries an idempotency key or is deduplicated by
``external_event_id`` on the server.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx

from ai_memory.errors import MemoryAPIError, MemoryConfigError, MemoryTimeoutError
from ai_memory.models import (
    AgentSession,
    Customer,
    Customer360,
    CustomerContext,
    EventExplanation,
    Goal,
    Health,
    Memory,
    QueryResult,
    Recommendation,
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
        self, customer_id: str, question: str, *, limit: int = 10, include_trace: bool = False
    ) -> QueryResult:
        return QueryResult.from_api(
            self._request(
                "POST",
                "/v1/memory/query",
                json={
                    "customer_id": customer_id,
                    "query": question,
                    "limit": limit,
                    "include_trace": include_trace,
                },
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
        self, customer_id: str, question: str, *, limit: int = 10, include_trace: bool = False
    ) -> QueryResult:
        return QueryResult.from_api(
            await self._request(
                "POST",
                "/v1/memory/query",
                json={
                    "customer_id": customer_id,
                    "query": question,
                    "limit": limit,
                    "include_trace": include_trace,
                },
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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncMemoryClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
