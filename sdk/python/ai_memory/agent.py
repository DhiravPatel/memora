"""The memory loop around an agent, done for you (§26 3.6).

Every integration ends up writing the same loop: before the model replies, find out what is
known about the customer; after it replies, record the turn; before it *acts*, ask whether
it may; when the conversation ends, leave a summary for next time. :class:`MemoryAgent`
is that loop, around whatever model or framework you use. It never calls a model itself —
you pass your own function — so it works the same with any provider, or none.

```python
from ai_memory import MemoryAgent, MemoryClient, ActionDenied, ApprovalRequired

client = MemoryClient(api_key=os.environ["MEMORY_API_KEY"])

with MemoryAgent(client, "cus_123", agent="support-bot", conversation_id=ticket.id) as memory:
    turn = memory.before_turn(ticket.message)          # records it, returns context
    reply = my_model(system=turn.prompt, user=ticket.message)
    memory.after_turn(reply)                            # records the reply

    try:
        memory.guard("offer_discount", amount=20)       # raises if not allowed
        apply_discount(...)
    except ApprovalRequired as pending:
        tell_customer("A colleague will confirm this shortly.")
    except ActionDenied as refused:
        log.info("not offering a discount: %s", refused.check.summary)
```
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ai_memory.errors import ActionDenied, ApprovalRequired, MemoryConfigError
from ai_memory.models import ActionCheck, AgentSession, TurnResult

_JSON = (str, int, float, bool, type(None))


@dataclass(slots=True)
class TurnContext:
    """What to put in front of your model for one turn."""

    # The briefing on the customer plus what is relevant to this message: a system prompt.
    prompt: str
    # Memora's own evidence-backed answer to the message, when it had one.
    answer: str | None = None
    memory_ids: list[str] = field(default_factory=list)
    session_id: str = ""


def _details(request: Mapping[str, Any] | None, extra: Mapping[str, Any]) -> dict[str, Any]:
    """The proposed action as JSON — only plain values go to the rules."""
    merged = {**dict(request or {}), **extra}
    return {key: value for key, value in merged.items() if isinstance(value, _JSON)}


def _prompt(briefing: str, result: TurnResult) -> str:
    blocks = [briefing]
    if result.context is not None and result.context.text:
        blocks.append("Relevant to this message:\n" + result.context.text)
    return "\n\n".join(block for block in blocks if block)


class MemoryAgent:
    """One conversation with one customer, with memory and guardrails wired in."""

    def __init__(
        self,
        client: Any,
        customer_id: str,
        *,
        agent: str = "agent",
        conversation_id: str | None = None,
        channel: str | None = None,
        token_budget: int | None = None,
        write_summary: bool = True,
    ) -> None:
        if not customer_id:
            raise MemoryConfigError("customer_id is required.")
        self.client = client
        self.customer_id = customer_id
        self.agent = agent
        self.conversation_id = conversation_id
        self.channel = channel
        self.token_budget = token_budget
        self.write_summary = write_summary
        self.session: AgentSession | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> AgentSession:
        """Open the session — or resume it, when ``conversation_id`` was seen before."""
        if self.session is None:
            self.session = self.client.open_session(
                self.customer_id,
                agent=self.agent,
                external_id=self.conversation_id,
                channel=self.channel,
                token_budget=self.token_budget,
            )
        return self.session

    @property
    def briefing(self) -> str:
        """What is known about the customer, including earlier conversations."""
        return self.start().prompt_text

    def end(self, outcome: str | None = None) -> AgentSession | None:
        """Close the session and leave a summary for the next conversation."""
        if self.session is None or not self.session.is_open:
            return self.session
        self.session = self.client.close_session(
            self.session.id, outcome=outcome, write_summary=self.write_summary
        )
        return self.session

    def __enter__(self) -> MemoryAgent:
        self.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        self.end(f"ended with {exc_type.__name__}" if exc_type else None)

    # --------------------------------------------------------------- turns

    def before_turn(self, message: str, *, remember: bool | None = None) -> TurnContext:
        """Record the customer's message and return the context to reply with."""
        session = self.start()
        result = self.client.add_turn(session.id, message, role="user", remember=remember, retrieve=True)
        return TurnContext(
            prompt=_prompt(self.briefing, result),
            answer=result.answer,
            memory_ids=list(result.context.memory_ids) if result.context else [],
            session_id=session.id,
        )

    def after_turn(self, reply: str) -> None:
        """Record what the agent said. Agent turns are kept, never learned as facts."""
        self.client.add_turn(self.start().id, reply, role="agent", retrieve=False)

    def respond(self, message: str, model: Callable[[str, str], str]) -> str:
        """``before_turn`` → ``model(prompt, message)`` → ``after_turn``, in one call."""
        turn = self.before_turn(message)
        reply = model(turn.prompt, message)
        self.after_turn(reply)
        return reply

    # ------------------------------------------------------------- actions

    def check(self, action: str, request: Mapping[str, Any] | None = None, **details: Any) -> ActionCheck:
        """Ask whether an action is allowed, without raising. Filed with this session."""
        return self.client.check_action(
            self.customer_id, action, _details(request, details), session_id=self.start().id
        )

    def guard(
        self,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        wait: float = 0.0,
        interval: float = 5.0,
        **details: Any,
    ) -> ActionCheck:
        """Return only if the action is allowed; raise otherwise.

        Raises :class:`ActionDenied` when a rule refuses, and :class:`ApprovalRequired`
        when a person must decide first. With ``wait`` (seconds) it waits for that person
        and, once they approve, redeems the approval — so the call returns only when the
        action may really go ahead.
        """
        payload = _details(request, details)
        verdict = self.check(action, payload)
        if verdict.allowed:
            return verdict
        if verdict.denied or verdict.approval is None:
            raise ActionDenied(verdict) if verdict.denied else ApprovalRequired(verdict)

        approval = verdict.approval
        if wait > 0 and approval.is_pending:
            approval = self.client.wait_for_approval(approval.id, timeout=wait, interval=interval)
        if approval.status in ("approved", "rejected"):
            # Redeeming re-runs the rules on today's facts: a yes from a person does not
            # override a refusal that has appeared since.
            redeemed = self.client.check_action(
                self.customer_id, action, payload, approval_id=approval.id, session_id=self.start().id
            )
            if redeemed.allowed:
                return redeemed
            raise ActionDenied(redeemed) if redeemed.denied else ApprovalRequired(redeemed)
        raise ApprovalRequired(verdict)

    def guarded(self, action: str, *, wait: float = 0.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorate a tool so it runs only when ``action`` is allowed; its keyword
        arguments are the request the rules see (``amount=``, ``channel=``…)."""

        def decorate(tool: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(tool)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                self.guard(action, kwargs, wait=wait)
                return tool(*args, **kwargs)

            return wrapper

        return decorate


class AsyncMemoryAgent:
    """:class:`MemoryAgent` for :class:`ai_memory.AsyncMemoryClient`."""

    def __init__(
        self,
        client: Any,
        customer_id: str,
        *,
        agent: str = "agent",
        conversation_id: str | None = None,
        channel: str | None = None,
        token_budget: int | None = None,
        write_summary: bool = True,
    ) -> None:
        if not customer_id:
            raise MemoryConfigError("customer_id is required.")
        self.client = client
        self.customer_id = customer_id
        self.agent = agent
        self.conversation_id = conversation_id
        self.channel = channel
        self.token_budget = token_budget
        self.write_summary = write_summary
        self.session: AgentSession | None = None

    async def start(self) -> AgentSession:
        if self.session is None:
            self.session = await self.client.open_session(
                self.customer_id,
                agent=self.agent,
                external_id=self.conversation_id,
                channel=self.channel,
                token_budget=self.token_budget,
            )
        return self.session

    async def end(self, outcome: str | None = None) -> AgentSession | None:
        if self.session is None or not self.session.is_open:
            return self.session
        self.session = await self.client.close_session(
            self.session.id, outcome=outcome, write_summary=self.write_summary
        )
        return self.session

    async def __aenter__(self) -> AsyncMemoryAgent:
        await self.start()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        await self.end(f"ended with {exc_type.__name__}" if exc_type else None)

    async def before_turn(self, message: str, *, remember: bool | None = None) -> TurnContext:
        session = await self.start()
        result = await self.client.add_turn(session.id, message, role="user", remember=remember, retrieve=True)
        return TurnContext(
            prompt=_prompt(session.prompt_text, result),
            answer=result.answer,
            memory_ids=list(result.context.memory_ids) if result.context else [],
            session_id=session.id,
        )

    async def after_turn(self, reply: str) -> None:
        session = await self.start()
        await self.client.add_turn(session.id, reply, role="agent", retrieve=False)

    async def respond(self, message: str, model: Callable[[str, str], Awaitable[str]]) -> str:
        turn = await self.before_turn(message)
        reply = await model(turn.prompt, message)
        await self.after_turn(reply)
        return reply

    async def check(self, action: str, request: Mapping[str, Any] | None = None, **details: Any) -> ActionCheck:
        session = await self.start()
        return await self.client.check_action(
            self.customer_id, action, _details(request, details), session_id=session.id
        )

    async def guard(
        self,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        wait: float = 0.0,
        interval: float = 5.0,
        **details: Any,
    ) -> ActionCheck:
        payload = _details(request, details)
        verdict = await self.check(action, payload)
        if verdict.allowed:
            return verdict
        if verdict.denied or verdict.approval is None:
            raise ActionDenied(verdict) if verdict.denied else ApprovalRequired(verdict)
        approval = verdict.approval
        if wait > 0 and approval.is_pending:
            approval = await self.client.wait_for_approval(approval.id, timeout=wait, interval=interval)
        if approval.status in ("approved", "rejected"):
            session = await self.start()
            redeemed = await self.client.check_action(
                self.customer_id, action, payload, approval_id=approval.id, session_id=session.id
            )
            if redeemed.allowed:
                return redeemed
            raise ActionDenied(redeemed) if redeemed.denied else ApprovalRequired(redeemed)
        raise ApprovalRequired(verdict)
