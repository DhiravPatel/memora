# API reference

Base URL: `http://localhost:8000` in development. Interactive docs: `/docs`.

## Authentication

| Caller | Header | Scope |
| --- | --- | --- |
| Your backend | `X-API-Key: mk_live_…` | exactly one project, limited by the key's scopes |
| The dashboard | `Authorization: Bearer <jwt>` | one organization, limited by the user's role |

API keys are stored as HMAC digests. The raw key is returned once, at creation, and never
again.

### Scopes

A key carries a set of scopes and is refused (`403 authorization_error`) when it tries
something outside them.

| Scope | Allows |
| --- | --- |
| `events:write` | `POST /v1/events`, `/batch`, `/retry`, integration webhooks |
| `customers:read` | Customer profiles, timelines, health, links, exports |
| `customers:write` | Create, bulk upsert, merge and delete customers |
| `memory:read` | `/v1/memories`, `/v1/memory/query`, `/search`, `/context` |
| `memory:write` | Create memories, submit feedback, delete memories |
| `memory:restricted` | Read memories the project's policy marked restricted |
| `approvals:decide` | Approve or reject actions agents asked permission for |
| `admin` | Everything, including webhook configuration — **except** `memory:restricted` and `approvals:decide` |

`memory:restricted` and `approvals:decide` are the scopes `admin` does not imply. Clearance
to read restricted memory is granted deliberately or not at all, because the key a project
is created with is an admin key and it tends to end up in every backend config — including
an agent's, which must never be able to approve its own requests. See
[Restricted memory](#restricted-memory) and [Approvals](#approvals).

A key can also act as an **agent profile** (`agent_profile_id` when creating or patching the
key). The profile narrows it: only the profile's memory types, restricted memory only if the
profile consents as well, and the profile's action lists in every guardrail check. See
[Agent profiles](#agent-profiles).

Send `X-Agent-Name: billing-bot` to label an unbound key's runs and checks; a bound key is
always labelled by its profile.

New keys default to `events:write`, `memory:read`, `customers:read`. Manage them at
`/v1/projects/{project_id}/api-keys` (dashboard auth) — create, list, re-scope, revoke. A
project's last active key cannot be revoked.

## Ingestion

### `POST /v1/events` → `202`

```json
{
  "customer_id": "cus_123",
  "event_type": "feature_used",
  "external_event_id": "evt_456",
  "occurred_at": "2026-09-17T10:00:00Z",
  "data": { "feature": "campaign_builder" }
}
```

```json
{ "event_id": "evt_internal_123", "status": "accepted", "importance": 0.25, "queued": true }
```

`customer_id` is *your* identifier; the customer is created on first use. Re-sending the
same `external_event_id` returns `200` with `"status": "duplicate"` and stores nothing.

### `POST /v1/events/batch`

Up to 500 events per request; each is deduplicated independently.

### Idempotency and limits

* `Idempotency-Key: <your id>` replays the original response for 24 hours. The same key
  with a different body is a `409`.
* `external_event_id` deduplicates at the domain level — the same event is stored once.
* Every API-key response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
  `X-RateLimit-Reset`. Over the limit is `429` with `retry_after` in the error details.
* `POST /v1/events/{event_id}/retry` re-queues a failed event. Replay is always safe:
  events are immutable and consolidation is idempotent per event id.

### `POST /v1/events/preview` → `200`

A dry run. Writes nothing — no event, no memory, no entity — and tells you what the event
*would* do.

```json
{ "customer_id": "cus_123", "event_type": "support_message",
  "data": { "message": "The Shopify sync keeps failing at checkout." } }
```

```json
{
  "would_process": true,
  "stop_reason": null,
  "summary": "1 new memory, 1 new entity.",
  "importance": 0.75,
  "threshold": 0.20,
  "text": "message: The Shopify sync keeps failing at checkout.",
  "redacted": false,
  "redactions": [],
  "memories": [{
    "content": "The Shopify sync keeps failing at checkout.",
    "type": "problem",
    "action": "create",
    "reason": "No sufficiently similar memory exists.",
    "rule": "below_threshold",
    "similarity": 0.34,
    "closest_memory_id": "mem_123",
    "closest_content": "The customer's Shopify sync stopped working…",
    "sensitivity": "normal",
    "extracted_by": "text:problem"
  }],
  "entities": [{ "name": "Shopify", "type": "product", "status": "existing" }]
}
```

**Read `stop_reason` first.** When it is set, nothing else happened and it says why —
the importance was below your threshold (both numbers are named), the payload had no
readable values, or the customer is gone.

`action` is `create`, `merge`, `update`, `supersede`, `conflict` or `ignore`.
`closest_memory_id` is the memory being changed when the action is not `create`, and the
*nearest miss* when it is — which is how you find out that a near-duplicate was created
because the consolidation threshold is set higher than your wording actually varies.

This is the real pipeline stopped before it writes, not a simulation, so what it reports is
what will happen. Needs `memory:read` as well as `events:write`, because it reads existing
memories to decide what a candidate would merge into.

## Customer 360

### `GET /v1/customers/{customer_id}/360`

Everything worth knowing about one customer, in one call. This is what an AI agent reads
before it replies, instead of calling eight endpoints and stitching the result together.

```json
{
  "customer": { "external_id": "cus_123", "name": "Acme", "last_event_at": "…" },
  "summary": "Acme: health 41/100 (at_risk), 3 open problems, 2 active goals.",
  "withheld": 0,
  "generated_at": "…",
  "sections": {
    "health": { "score": 41.0, "band": "at_risk", "churn_risk": 0.62, "factors": [], "explanation": "…" },
    "subscription": { "content": "The customer upgraded to Pro.", "history": [] },
    "active_problems": [{ "id": "mem_1", "content": "…", "confidence": 0.9, "evidence_count": 4 }],
    "goals": [{ "statement": "Roll out to the whole company", "status": "progressing" }],
    "preferences": [],
    "important_memories": [],
    "relationships": [{ "relation": "uses", "entity": "Shopify", "entity_type": "product" }],
    "recent_activity": [{ "type": "support_message", "outcome": "1 new memory." }],
    "risk_signals": { "trajectory": "declining", "churn_risk": 0.62, "observations": [] },
    "recommended_actions": [{ "action": "…", "rationale": "…", "urgency": 0.8, "playbook": [] }],
    "recent_conversations": [{ "agent": "support-bot", "summary": "…" }]
  }
}
```

**`?include=health,active_problems`** builds only those sections. The response goes into
somebody's context window, so the sections you do not need are worth not asking for — a
full 360 is roughly 3,000 tokens, a filtered one a few hundred. An unknown section name is
a `422`, not a quietly shorter response.

A section that was not requested is **absent**, not empty, so `"active_problems": []`
always means "none" rather than "not asked for".

Every section comes from the endpoint that already owns it, so the numbers here are the
numbers there — `sections.health` is exactly what `/health` returns. Restricted memories
(see the security guide) are filtered out with a `withheld` count; the health score is not
gated, because it is a number about a customer rather than a quote from one.

Needs `customers:read` and `memory:read`.

## Facts and conditions

The language guardrails, the lifecycle, workflows and feature flags are written in.

```text
health.score < 60 and problems.entities contains "shopify"
not (subscription.plan in ["enterprise", "business"])
problems.oldest_open_days between 7 and 30
customer.metadata.segment == "smb" and preferences.channel is set
```

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/conditions/catalog` | every fact, its type and the operators it accepts |
| `POST /v1/conditions/validate` | `{condition}` → `valid`, canonical `text`, or `error` with the character `position` |
| `POST /v1/conditions/evaluate` | `{customer_id, condition}` → outcome, the deciding clauses and their evidence |
| `GET /v1/customers/{customer_id}/facts` | the whole fact document, with evidence per fact |

`outcome` is `true`, `false` or `unknown` — unknown when a fact the condition reads has no
value yet. Act on `matched`, which treats unknown as false. A condition is evaluated on the
facts the caller may see: without `memory:restricted`, values that came only from
restricted memories are removed (`withheld_facts` lists which), so a condition cannot be
used to probe one. Needs `memory:read`.

## Lifecycle

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/customers/{customer_id}/state` | current state, the transition that caused it and its evaluation |
| `GET /v1/customers/{customer_id}/state/history` | every stay in a state, newest first |
| `PUT /v1/customers/{customer_id}/state` | `{state, pin?, pin_days?, note?}` — set by hand; pinned by default |
| `DELETE /v1/customers/{customer_id}/state/pin` | hand the customer back to the machine |
| `POST /v1/customers/{customer_id}/state/refresh` | re-evaluate now |
| `GET /v1/lifecycle` | the machine and how many customers are in each state |
| `GET /v1/lifecycle/customers?state=at_risk` | who is in a state |

The machine is the `lifecycle` project setting — states, an initial state and ordered
transitions whose `when` is a condition. The first matching transition wins. Writes need
`customers:write`.

## Snapshots

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/customers/{customer_id}/snapshots` | every material change, newest first, each with what changed |
| `GET /v1/customers/{customer_id}/snapshots/at?time=` | what was known at a moment in the past |
| `GET /v1/customers/{customer_id}/snapshots/{snapshot_id}` | one snapshot with its full fact document |

A snapshot is taken only when something material changes — a band, a count, the state, the
plan — so each one is a moment worth knowing about. Needs `memory:read`.

## Quality

### `GET /v1/quality?days=30`

A score out of 100, its six parts, and — for every part that is off — a diagnostic naming
the cause and a concrete fix:

```json
{
  "score": 82,
  "components": [{ "key": "yield", "label": "Extraction yield", "score": 64, "detail": "…" }],
  "diagnostics": [{
    "key": "threshold_filtering",
    "severity": "warning",
    "title": "31% of `support_message` events are never read",
    "detail": "…scored below this project's importance threshold of 0.20…",
    "fix": { "action": "set", "setting": "event_importance.support_message", "value": 0.3 }
  }],
  "metrics": { "events": {}, "consolidation": {}, "memories": {}, "searches": {}, "evaluation": null }
}
```

A part with nothing to measure yet has `score: null` and is left out of the overall score.
Needs `memory:read`.

## Retrieval evaluation

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/evals` / `POST /v1/evals` | list sets (with the latest run), create one |
| `GET /v1/evals/{set_id}` / `DELETE` | a set with its cases and runs |
| `POST /v1/evals/{set_id}/cases` | `{cases: [{customer_id, question, expected_memory_ids?, expected_phrases?}]}` |
| `DELETE /v1/evals/{set_id}/cases/{case_id}` | remove a case |
| `POST /v1/evals/{set_id}/runs` | `{label?, k?, wait?}` — run the set; inline up to 100 cases |
| `GET /v1/evals/{set_id}/runs` | runs, newest first |
| `GET /v1/evals/runs/{run_id}` | a run with per-question results and the comparison |
| `GET /v1/evals/suggestions` | recent real questions and what came back — pick the right answer to make a case |

A run reports Recall@k, Hit@k, MRR and citation hit rate, the retrieval settings it ran
under, and `comparison` against the previous run. **`comparison.regressed` is true when any
question that used to be answered no longer is** — the flag to fail a CI job on. Runs ask
through the real answer path without writing to the query log or counting as usage.
Writes need `memory:write`.

## Reading

| Endpoint | Returns |
| --- | --- |
| `GET /v1/customers/{customer_id}` | profile |
| `GET /v1/customers/{customer_id}/memories` | `type`, `status`, `limit`, `offset` |
| `GET /v1/customers/{customer_id}/timeline` | events and memories, newest first |
| `GET /v1/customers/{customer_id}/graph` | nodes and edges for the memory graph |
| `GET /v1/memories/{memory_id}` | a memory with its full version history |
| `GET /v1/events` | raw events and processing status |
| `GET /v1/events/{event_id}` | one event, with `outcome` — why it did or did not become a memory |
| `GET /v1/customers/{customer_id}/health` | health score, band, churn risk and the factors behind them |
| `GET /v1/customers/{customer_id}/links` | inferred links and causal chains between memories |
| `GET /v1/customers/{customer_id}/export` | everything known about a customer, including version history |

## Forecasting

Health says where a customer is; these say which way they are moving and what to do about
it. Both are derived from the same memories the other endpoints return, so they can never
disagree with what you are looking at.

| Endpoint | Returns |
| --- | --- |
| `GET /v1/customers/{customer_id}/signals` | trajectory, churn risk, expansion score, confidence, the ranked signals and the daily history (`?series=false` to skip it) |
| `GET /v1/customers/{customer_id}/recommendations` | up to five ranked actions, each with its rationale, evidence and playbook |

```json
{
  "trajectory": "declining",
  "churn_risk": 0.86,
  "expansion_score": 0.06,
  "confidence": 0.62,
  "headline": "Declining: 3 problems reported in the last 14 days against 0 in the 14 before; cancellation or competitor language 2 days ago.",
  "signals": [
    {
      "key": "escalating_problems",
      "direction": "risk",
      "strength": 1.0,
      "horizon_days": 14,
      "rationale": "3 problems reported in the last 14 days against 0 in the 14 before",
      "memory_ids": ["mem_…"]
    }
  ]
}
```

## Goals

A goal is opened when a customer says what they are trying to do, and closed only by later
evidence — a message that says it happened rarely mentions the goal itself.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/customers/{customer_id}/goals` | this customer's goals, live ones first |
| `GET /v1/goals` | every tracked goal in the project (`?status=`) |
| `GET /v1/goals/summary` | counts by status |
| `PATCH /v1/goals/{goal_id}` | set a status by hand (`memory:write`) |

Statuses are `open` → `progressing` → `achieved` | `abandoned`, with `stalled` for a goal
nothing has moved for 30 days. A `PATCH` is treated as a human verdict: the tracker stops
touching that goal, so a correction sticks.

## Agent sessions

Memory that survives between conversations. Three calls:

```ts
const session = await memory.agent.open({ customerId, externalId: conversationId });
const reply = await llm(session.context!.text, userMessage);   // briefed from memory
await memory.agent.turn(session.id, { role: "user", content: userMessage });
await memory.agent.turn(session.id, { role: "agent", content: reply });
await memory.agent.close(session.id, { outcome: "Escalated to engineering" });
```

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/agent/sessions` | open or resume; returns the customer context and the last three session summaries |
| `POST /v1/agent/sessions/{id}/turns` | record a turn; customer turns become memory and come back answered |
| `POST /v1/agent/sessions/{id}/close` | close, writing a summary memory for the next session |
| `GET /v1/agent/sessions[/{id}]` | list, or one session with its transcript |

Opening with the same `external_id` resumes rather than duplicating, so it is safe to call
on every reconnect. Customer turns are remembered by default; agent turns are recorded but
not learned from. Closing is what makes the *next* conversation continuous — the summary is
stored as an ordinary `summary` memory with `source: "agent"`.

Writes on this surface need `memory:write`; reads need `memory:read`.

## Agent profiles

What an agent is for. Bound to a key, never chosen per request.

```json
POST /v1/agent/profiles
{ "name": "support-agent", "readable_types": ["problem", "preference", "subscription", "fact"],
  "can_read_restricted": false, "denied_actions": ["offer_discount", "process_refund"] }
```

| Field | Meaning |
| --- | --- |
| `readable_types` | Memory types the key may read, through every endpoint. Empty: all of them |
| `can_read_restricted` | Consent to restricted memory; the key still needs `memory:restricted` |
| `allowed_actions` | The only actions guardrail checks will allow. Empty: any not denied |
| `denied_actions` | Actions always refused |

`GET /v1/agent/profiles`, `GET /v1/agent/profiles/me` (the profile this key acts as, or
`null`), `PATCH` and `DELETE /v1/agent/profiles/{id}` — writes need `admin`. A profile with
live keys bound cannot be deleted (it would widen them). Bind keys with
`PATCH /v1/projects/{id}/api-keys/{key_id} {"agent_profile_id": "agp_…"}` (dashboard auth);
a bound key cannot hold `approvals:decide`.

## Guardrails

Ask before acting.

```json
POST /v1/agent/check
{ "customer_id": "cus_123", "action": "offer_discount", "request": { "amount": 300 } }
```

```json
{
  "id": "chk_…", "decision": "deny", "allowed": false,
  "summary": "No discounts over 200 for critical accounts without the CS lead.",
  "reasons": [
    { "rule": "large_discounts", "source": "project", "decision": "deny",
      "explanation": "No discounts over 200 for critical accounts without the CS lead.",
      "evidence": ["mem_…"], "evaluation": { "outcome": "true", "explanation": "request.amount > 200: true (is 300.0); …" } },
    { "rule": "money_requires_approval", "source": "builtin", "decision": "require_approval",
      "explanation": "A discount of 300 needs a person's approval.", "evidence": [] }
  ],
  "evidence": ["mem_…"], "approval": null, "agent": "support-agent", "checked_at": "…"
}
```

`decision` is the strictest reason: `deny` > `require_approval` > `allow`. Every reason is
returned, not just the deciding one. `request` carries what rules read — `channel`,
`amount`, `topic`, `plan`, or anything else (`request.<key>` in a project rule).
`"dry_run": true` decides without recording or filing an approval.

Built-in rules (switch any off in the `guardrails` setting):

| Rule | Decision |
| --- | --- |
| `open_problem_blocks_selling` | deny selling with an open problem |
| `at_risk_blocks_selling` | deny selling to an at-risk customer |
| `churn_intent_blocks_promotion` | deny marketing to a customer who said they may leave |
| `channel_preference` | deny contact on a channel the customer does not prefer |
| `unresolved_problem_blocks_closing` | deny closing a ticket whose problem is open; approval when the topic is not given |
| `money_requires_approval` | discounts, credits, refunds, waived fees |
| `account_change_requires_approval` | cancel, downgrade, change plan, pause, delete |

Project rules, in the `guardrails` setting:

```json
{ "disabled": [], "approval_ttl_hours": 24,
  "rules": [ { "name": "large_discounts", "actions": ["offer_discount"],
               "when": "request.amount > 200 and health.band == \"critical\"",
               "decision": "deny", "message": "No discounts over 200 for critical accounts without the CS lead." } ] }
```

Conditions are compiled on save (`422` naming the fact you meant). `GET /v1/agent/checks`
lists recorded checks (`customer_id`, `decision`, `agent`, `session_id`);
`GET /v1/agent/guardrails` returns the recognised actions and built-in rules.

## Approvals

A `require_approval` check returns `"approval": {"id": "apr_…", "status": "pending", …}`.
Retrying the same request returns the same approval.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/agent/approvals?status=pending` | the queue |
| `GET /v1/agent/approvals/{id}` | poll while waiting, or subscribe to `agent.approval_decided` |
| `POST /v1/agent/approvals/{id}/decision` | `{"decision": "approve" \| "reject", "note": "…"}` — needs `approvals:decide` |

Then redeem: check again with `"approval_id": "apr_…"` and the **same** action and request.
The rules re-run on today's facts; an approval satisfies only `require_approval`, never a
`deny`; the first successful redemption marks it `used`. A different request is a `422`; a
rejected approval is a `deny`; an expired or used one files a new request. The legacy
project key, profile-bound keys and the key that asked can never decide.

## Agent runs

Every answer and context build, as it was recorded.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/agent/runs` | filter by `customer_id`, `agent`, `session_id`, `kind` (`query`/`context`), `since`, `until` |
| `GET /v1/agent/runs/{id}` | the run with its trace: each memory's rank, scores, strategies, citation |
| `GET /v1/agent/runs/{id}/explain` | why it said that — in sentences |

```json
{
  "narrative": [
    "support-agent asked: “Is the Shopify sync still broken?”.",
    "The answer was built from #1 “The Shopify sync stopped working after the upgrade.” (found by concept, semantic, score 0.72).",
    "Memory #1 was corrected after this run: it now reads “The Shopify sync was fixed on Monday.”.",
    "3 of this customer's memories were not visible to the support-agent profile when it asked.",
    "At the time the customer's recorded state was health 23 (critical), lifecycle at_risk, with 7 open problems."
  ],
  "memories": [ { "id": "mem_…", "rank": 1, "cited": true, "content_then": "…", "content_now": "…",
                  "status_now": "active", "changed_since": [ { "at": "…", "reason": "corrected by a person" } ] } ],
  "held_back": { "withheld": 3, "profile": "support-agent", "readable_types": ["fact", "problem"] },
  "state_then": { "id": "snp_…", "health_score": 23, "state": "at_risk", "open_problems": 7 },
  "checks": [ … ]
}
```

A memory a reader may not see is shown by id and score with `"visible": false`, and an
answer composed from one is `[withheld]`.

## MCP

The Python SDK ships an MCP server: `pip install ai-memory`, then

```bash
MEMORA_API_KEY=mk_live_… MEMORA_BASE_URL=https://memory.internal ai-memory-mcp          # stdio
ai-memory-mcp --transport http --port 8765                                             # Streamable HTTP
```

Tools: `ask_memory`, `search_memory`, `customer_360`, `customer_brief`, `customer_changes`,
`customer_timeline`, `get_health`, `get_goals`, `get_recommendations`, `check_action`,
`explain_answer`, `remember`. Over HTTP each caller sends their own key as
`Authorization: Bearer mk_…`; the key's scopes, clearance and profile apply to every tool.

## Asking

### `POST /v1/memory/query`

```json
{ "customer_id": "cus_123", "query": "What problems has this customer experienced recently?" }
```

```json
{
  "answer": "The customer has repeatedly experienced problems connecting Shopify.",
  "confidence": 0.8,
  "memories": [{ "id": "mem_123", "content": "…", "score": 0.82, "retrieved_by": ["semantic", "keyword"] }],
  "sources": [{ "event_id": "evt_123" }]
}
```

Pass `"include_trace": true` to get the query analysis, the strategies used and the
per-signal ranking breakdown. Every answer is recorded as an [agent run](#agent-runs):
the response carries `run_id`, and `session_id` in the request files it with an agent
session.

### `POST /v1/memory/search`

Hybrid retrieval without answer composition — use it when you want to build your own prompt.

### `POST /v1/memory/context`

```json
{ "customer_id": "cus_123", "task": "respond_to_support_ticket", "query": "Shopify is broken again." }
```

Returns a token-bounded, sectioned view of the customer. Pass `"format": "text"` for a
ready-to-paste prompt block, and `token_budget` to fit your own agent's context window. Like
an answer, a context build is recorded as a run and returns `run_id`.

## Feedback

### `POST /v1/memories/{memory_id}/feedback`

```json
{ "verdict": "confirm" }
{ "verdict": "reject", "note": "the customer never said this" }
{ "verdict": "correct", "content": "The Shopify OAuth token expires every 24 hours." }
```

`confirm` raises confidence, `reject` lowers it (retiring the memory below 0.25), and
`correct` creates a manual memory that supersedes the original. Every verdict writes a
memory version and an audit entry.

## Integrations

### `POST /v1/integrations/{provider}/webhook`

Providers: `stripe`, `intercom`, `zendesk`, `slack`, `posthog`, `hubspot`, `generic`.

Authenticated twice — the project API key in `X-API-Key`, and the provider's own signature
header verified against `settings.integrations.<provider>.signing_secret`. Payloads are
normalised into the internal event schema and ingested through the same path as
`POST /v1/events`; the response is the batch-accept shape.

`GET /v1/integrations` lists the providers and which of them are configured.

## Customers

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/customers/batch` | Create or update up to 500 customers in one request |
| `POST /v1/customers/{customer_id}/merge` | Merge a duplicate into another record |

```json
POST /v1/customers/cus_duplicate/merge
{ "into": "cus_primary" }

→ { "source_customer_id": "cus_duplicate", "target_customer_id": "cus_primary",
    "events_moved": 42, "memories_moved": 9, "links_moved": 5 }
```

The merged id keeps resolving: the source record survives as a tombstone pointing at the
survivor.

## Outbound webhooks

Subscribe to changes rather than polling for them. Configure at
`/v1/projects/{project_id}/webhooks` (dashboard auth).

| Event | Fires when |
| --- | --- |
| `memory.created` / `memory.updated` | A memory was written or gained evidence |
| `memory.conflict` | A contradiction superseded an existing memory |
| `customer.created` | A customer was seen for the first time |
| `customer.at_risk` / `customer.recovered` | Health crossed into or out of at_risk |
| `customer.health_changed` | Any other band change |
| `event.failed` | An event exhausted its processing retries |
| `customer.deleted` | A customer and everything derived from them was deleted |
| `goal.achieved` / `goal.abandoned` | A tracked goal reached its end state (progress does not fire) |
| `signal.raised` | A strong risk signal appeared that was not in the previous snapshot |
| `customer.state_changed` | The customer moved through the lifecycle — by a rule, or set by hand |
| `agent.action_denied` | A guardrail check refused an action an agent proposed |
| `agent.approval_requested` | An agent needs a person to approve an action |
| `agent.approval_decided` | A request was approved, rejected — or lapsed (`status: "expired"`) |

Every request carries:

```
X-Memora-Signature: t=1789739686,v1=<hmac-sha256 of "<t>.<raw body>">
X-Memora-Timestamp: 1789739686
X-Memora-Event:     customer.at_risk
X-Memora-Delivery:  evn_…
```

Verify with the SDK rather than by hand:

```ts
import { constructWebhookEvent } from "@ai-memory/sdk";

const event = await constructWebhookEvent({
  secret: process.env.MEMORY_WEBHOOK_SECRET!,
  payload: await request.text(),          // the raw body, before parsing
  signatureHeader: request.headers.get("x-memora-signature") ?? "",
});
```

```python
from ai_memory import construct_event

event = construct_event(
    secret=os.environ["MEMORY_WEBHOOK_SECRET"],
    payload=request.data,
    signature_header=request.headers["X-Memora-Signature"],
)
```

Deliveries retry on failure (30s → 2m → 10m → 1h → 3h → 6h) and every attempt is recorded
at `/v1/projects/{project_id}/webhooks/deliveries`.

## Live events

```
GET /v1/projects/{project_id}/events/stream?since_seconds=30
Accept: text/event-stream
Authorization: Bearer <token>
```

Server-sent events. Replays the last `since_seconds` of history, then streams each event as
it arrives and again when the worker finishes with it — `data.change` is `created` or
`processed`. A `heartbeat` frame every 15 s keeps proxies from closing an idle connection,
and the server closes the stream after 10 minutes so clients reconnect (and re-authenticate).

Dashboard auth only, and the token goes in the header: read it with `fetch` and a stream
reader rather than `EventSource`, which cannot set headers and would force the token into
the URL.

## Restricted memory

A project can mark some memories readable only with clearance — anything of a given type,
anything mentioning certain words, or anything matching a pattern. Rules live in
`restriction_policies` (see the security guide); this is what the API does about them.

Every memory carries a `sensitivity` of `normal` or `restricted`, and a restricted one
names the rule that gated it in `metadata.restricted_by`.

Without the `memory:restricted` scope a restricted memory is not returned anywhere: not in
`/v1/memories`, not by id (`404`, the same as one that does not exist), not in an answer,
a context block, a search result or an export. List endpoints report how many were hidden:

```json
{ "data": [ … ], "total": 12, "limit": 50, "offset": 0, "withheld": 3 }
```

`withheld` is `0` for a cleared caller and for any list with nothing to hide. It is
reported rather than silently omitted so that an incomplete list never looks complete.

The same rule covers everything that *quotes* a memory. For a caller who may not see a
memory — restricted without clearance, or outside their agent profile — facts drop the
values that came only from it, goals born from it are hidden, recommendation and signal
text shows `[withheld]` in place of its words, evaluation results and suggestions withhold
its snippet and any answer composed from it, memory links to it are left out, and an event
whose payload fed it is returned with `"data": {}` and `"withheld": true` (the timeline and
agent transcripts likewise). Numbers — health, risk, counts — are never redacted.

## Vocabulary

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/projects/{project_id}/vocabulary` | term pairs that go together, with score, support and origin |
| `POST /v1/projects/{project_id}/vocabulary` | add a pair by hand |
| `DELETE /v1/projects/{project_id}/vocabulary/{term}/{synonym}` | reject a pair mining got wrong |
| `POST /v1/projects/{project_id}/vocabulary/rebuild` | re-mine now instead of waiting for tonight |

Mostly mined rather than configured — the usual way to change it is to change what
customers write — but a pair nobody has written down yet can be added directly, and a wrong
one can be rejected. A rebuild replaces only what it mined, so both decisions survive it.
Retrieval uses the table to widen a question, so asking about "the loader" can reach
memories that only ever say "importer".

## Team and sessions

| Endpoint | Purpose |
| --- | --- |
| `GET/PATCH/DELETE /v1/organization/members[/{user_id}]` | List members, change roles, remove |
| `GET/POST/DELETE /v1/organization/invitations[/{id}]` | Invite, list, revoke |
| `POST /v1/auth/accept-invitation` | Join with a single-use token and set a password |
| `POST /v1/auth/change-password` | Rotate a password; all other sessions end |
| `POST /v1/auth/sign-out-everywhere` | Invalidate every outstanding token |

Roles are hierarchical (owner > admin > member > viewer). Nobody can grant a role above
their own, and an organization can never lose its last owner.

Inviting sends the invitee an email with the accept link. The response also contains the
link, and an `email_queued` flag — `false` means the queue was unreachable and no mail will
be sent, so that response is the only copy. Revoking tells the invitee their link is dead.
Mail is optional infrastructure: with no `SMTP_HOST` configured, messages are logged
instead (see the security guide).

## Deletion

`DELETE /v1/customers/{customer_id}` removes the customer, their events, memories, memory
versions, embeddings and graph links, and writes an audit entry. `DELETE /v1/memories/{id}`
removes a single memory.

## Errors

```json
{ "error": { "code": "not_found", "message": "Customer 'cus_9' not found." } }
```

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | `authentication_error` | missing or invalid credentials |
| 403 | `authorization_error` | valid credentials, insufficient role |
| 404 | `not_found` | unknown resource *in this project* |
| 409 | `conflict` | duplicate name or resource |
| 422 | `validation_error` | request body failed validation |
| 429 | `rate_limited` | upstream provider throttled the request |
| 502 | `provider_error` | an upstream dependency failed |

Every response carries `X-Request-Id`; include it when reporting a problem.
