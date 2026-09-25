# ai-memory (Python)

```bash
pip install ai-memory
```

```python
from ai_memory import MemoryClient

memory = MemoryClient(api_key=os.environ["MEMORY_API_KEY"])

# Track what happened
memory.track(
    customer_id="cus_123",
    type="support_message",
    data={"message": "I've tried connecting Shopify three times but it still doesn't work."},
    external_event_id="msg_456",   # idempotent
)

# Find out what an event would do, without sending it
preview = memory.preview(
    customer_id="cus_123",
    type="support_message",
    data={"message": "I've tried connecting Shopify three times."},
)
if not preview:                      # nothing would be remembered
    print(preview.stop_reason)       # ...and this says why
for plan in preview.memories:
    print(plan.action, plan.type, plan.content, plan.reason)

# Before a call or a reply: what to raise, what not to do and why, the next step
brief = memory.brief("cus_123")                 # since the last conversation, by default
print(brief.headline)    # "Acme: at risk (54), declining. On the Pro plan, customer for 8 months. …"
for point in brief.talking_points:
    print("-", point)
if brief.forbids("offer_upgrade"):              # the guardrails' own verdict, for this key's profile
    print(brief.caution_for("offer_upgrade").text)
prompt = memory.brief_markdown("cus_123")        # the same brief as a page, for a system prompt

# Everything worth knowing, in one call — what an agent reads before it replies
view = memory.customer_360("cus_123", include=["health", "active_problems", "goals"])
print(view.summary)
if view.is_at_risk:
    escalate(view.active_problems, view.recommended_actions)

# A rule, evaluated against a customer — the same language the lifecycle and guardrails use
if memory.evaluate_condition("cus_123", 'problems.entities contains "billing"'):
    hold_the_upsell()
print(memory.lifecycle_state("cus_123").state)      # e.g. "at_risk", with .reason
print(memory.lifecycle_state("cus_123", track="engagement").reasons)  # ["3 unresolved problems", …]

# What changed since the last conversation — typed changes, then and now
changes = memory.changes("cus_123", since="last_session", agent="support-bot")
print(changes.summary)   # "Since the last conversation (17 Sep 2026): upgraded from Starter to Pro; …"
for change in changes.of_type("problem", "subscription"):
    print(change.title, change.before, "→", change.after)

# Is memory any good? Measure retrieval and extraction, and fail CI on a regression
run = memory.run_eval(set_id, label="after vocabulary change")
assert not run["comparison"]["regressed"], run["comparison"]["newly_missed"]
# Before saving a setting: what would it break?
check = memory.eval_regression(set_id, {"consolidation_similarity": 0.4})
assert check["safe"], check["newly_failing"]

# Ask what it means
result = memory.query("cus_123", "Why is this customer unhappy?", include_trace=True)
print(result.answer, result.source_event_ids)

# Why did my agent do this? — including what it was not given
trace = memory.run_trace(result.run_id)
for item in trace.ignored_because("superseded", "withheld_profile"):
    print("✗", item["why"])

# Give an agent context before it replies
context = memory.context("cus_123", task="support_response", as_text=True)
print(context.prompt_text)

# Health and churn risk, derived from the same memories
health = memory.health("cus_123")
if health.is_at_risk:
    escalate(health.explanation)
```

An async client with the same surface is available as `AsyncMemoryClient`.

## Agents

Ask before acting, and let a person decide what a rule says needs one:

```python
from ai_memory import ActionDenied, ApprovalRequired, MemoryAgent

check = memory.check_action("cus_123", "offer_discount", {"amount": 50})
if check.denied:
    print(check.summary, check.evidence)         # why, and the memories behind it
elif check.requires_approval:
    approval = memory.wait_for_approval(check.approval.id, timeout=60)
    if approval.is_approved:                      # redeem it, once, for exactly this request
        memory.check_action("cus_123", "offer_discount", {"amount": 50}, approval_id=approval.id)

# Why did the agent say that? Every query and context call is a recorded run.
answer = memory.query("cus_123", "Is their Shopify sync fixed?")
print("\n".join(memory.explain_run(answer.run_id).narrative))
```

`MemoryAgent` runs the whole loop around your own model — brief, record, guard, summarise:

```python
with MemoryAgent(memory, "cus_123", agent="support-bot", conversation_id=ticket.id) as agent:
    reply = agent.respond(ticket.message, lambda prompt, message: my_model(system=prompt, user=message))
    try:
        agent.guard("offer_discount", amount=20)     # raises unless it may go ahead
    except ApprovalRequired:
        reply += " A colleague will confirm the discount shortly."
    except ActionDenied as refused:
        log.info("no discount: %s", refused.check.summary)
```

To *act*, go through the **gateway**: it decides like `check_action`, applies the customer's
opt-outs and the project's automatic limits, and records the action — so its outcome becomes
the customer's history (`actions.*` facts), which later rules and limits read. `perform`
does the whole round trip:

```python
with MemoryAgent(memory, "cus_123", agent="billing-bot") as agent:
    # Requests it, waits up to 2 minutes for a person if one is needed, runs the lambda only
    # once allowed, and reports "done" — or "failed" with the error, which is re-raised.
    agent.perform("issue_credit", lambda: billing.credit("cus_123", 20), amount=20, wait=120)

action = memory.request_action("cus_123", "process_refund", {"amount": 25}, idempotency_key=ticket.id)
if action.allowed:
    refund_id = billing.refund(25)
    memory.complete_action(action.id, "done", external_ref=refund_id)
elif action.waiting:
    action = memory.wait_for_action(action.id, timeout=300)   # proceeds once a person approves
```

A key bound to an **agent profile** reads only the profile's memory types everywhere and is
held to its actions; `memory.my_profile()` says which. Pass `agent_name="billing-bot"` to
`MemoryClient` to label an unbound key's runs.

## MCP server

Any MCP client — a desktop assistant, an IDE, an agent framework — gets Memora as tools:

```bash
MEMORA_API_KEY=mk_live_… MEMORA_BASE_URL=https://memory.internal ai-memory-mcp
ai-memory-mcp --transport http --port 8765     # hosted: each caller sends its own key as a Bearer token
```

Tools: `ask_memory`, `search_memory`, `customer_360`, `customer_brief`, `customer_changes`,
`customer_timeline`, `get_health`, `get_goals`, `get_recommendations`, `check_action`,
`request_action`, `proceed_action`, `report_action`, `explain_answer`, `remember`. The key's scopes, clearance and agent profile apply to all of
them. Standard library only — nothing beyond `httpx`.

The API key identifies exactly one project and must stay server-side.
