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
| `admin` | Everything, including webhook configuration — **except** `memory:restricted` |

`memory:restricted` is the one scope `admin` does not imply. Clearance to read restricted
memory is granted deliberately or not at all, because the key a project is created with is
an admin key and it tends to end up in every backend config. See
[Restricted memory](#restricted-memory).

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
per-signal ranking breakdown.

### `POST /v1/memory/search`

Hybrid retrieval without answer composition — use it when you want to build your own prompt.

### `POST /v1/memory/context`

```json
{ "customer_id": "cus_123", "task": "respond_to_support_ticket", "query": "Shopify is broken again." }
```

Returns a token-bounded, sectioned view of the customer. Pass `"format": "text"` for a
ready-to-paste prompt block, and `token_budget` to fit your own agent's context window.

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
