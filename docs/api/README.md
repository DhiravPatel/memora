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

## Customer brief

### `GET /v1/customers/{customer_id}/brief`

"Brief me on Acme" — the judgement, not the record: the situation in a few sentences, what
to raise and in what order, what **not** to do and why, and the next step, each from the
evidence. Built for the moment before a call or a reply.

| Parameter | Meaning |
| --- | --- |
| `since` | what `recent_changes` covers — `last_session` (default: since the last conversation that ended), `last_run`, a span (`7d`), an ISO time or a snapshot id; see [What changed](#what-changed) |
| `agent` | with `last_session`/`last_run`: only that agent's |
| `format` | `json` (default) or `markdown` — the brief as a page, `text/markdown` |

```json
{
  "customer": { "external_id": "acme", "name": "Acme", "customer_since": "…", "last_active_at": "…" },
  "headline": "Acme: critical (34), declining. On the Pro plan (upgraded from Starter 2 days ago), customer for 2 weeks. 3 open problems; said they may cancel.",
  "talking_points": [
    "They said they may leave: “The customer will cancel if the payroll export keeps failing”. Acknowledge it before anything else.",
    "Still open after 2 weeks, reported 3 times: “The payroll export failed again last night”.",
    "Since the last conversation (19 Sep 2026): upgraded from Starter to Pro; a new problem; a problem reported again; said they may cancel — and 5 more changes.",
    "Next step — Escalate: The payroll export failed again last night (reported 3 separate times without being closed out — first-line support has not been enough).",
    "They prefer email."
  ],
  "cautions": [
    { "text": "Don't offer an upgrade: the customer has 3 open problems; resolve them before selling.",
      "action": "offer_upgrade", "actions": ["offer_upgrade"], "decision": "deny",
      "summary": "The customer has 3 open problems; resolve them before selling.",
      "rules": ["open_problem_blocks_selling", "at_risk_blocks_selling"], "evidence": ["mem_…"] },
    { "text": "Don't call them: the customer asked not to be called.", "action": "call_customer",
      "actions": ["call_customer"], "decision": "deny", "rules": ["respect_opt_out"], "evidence": ["mem_…"] }
  ],
  "next_step": { "key": "escalate_repeat_problem", "action": "Escalate: …", "rationale": "…", "priority": "now", "memory_ids": ["mem_…"] },
  "set_aside": [{ "key": "retention_outreach", "action": "Get on a call about the renewal", "because": "The customer asked not to be called." }],
  "situation": {
    "health": { "score": 34.2, "band": "critical", "churn_risk": 0.78, "trajectory": "declining", "explanation": "…" },
    "plan": { "name": "pro", "statement": "The customer upgraded from the Starter plan to the Pro plan.", "direction": "upgraded" },
    "lifecycle": [{ "track": "lifecycle", "label": "Lifecycle", "state": "at_risk", "reasons": ["said they may cancel"] }],
    "open_problems": 3,
    "goals": { "open": 0, "progressing": 0, "stalled": 0, "achieved": 0 }
  },
  "open_issues": [{ "id": "mem_…", "content": "…", "age_days": 19, "times_reported": 3 }],
  "goals": [], "intents": [{ "id": "mem_…", "type": "intent", "content": "…", "kinds": ["cancellation"] }],
  "preferences": { "channel": "email", "opt_outs": [{ "kind": "phone", "words": "asked not to be called" }], "statements": [] },
  "risks": [], "opportunities": [],
  "recent_changes": { "window": { "basis": "last_session", "label": "Since the last conversation (19 Sep 2026)" }, "summary": "…", "items": [], "total": 15, "withheld": 0 },
  "last_conversation": { "agent": "support-bot", "summary": "…", "closed_at": "…" },
  "evidence": ["mem_…"],
  "withheld": 0, "withheld_facts": [],
  "markdown": "# Acme\n\nAcme: critical (34), declining. …"
}
```

**Cautions** are the guardrails' own verdicts ([Guardrails](#guardrails)) on selling,
marketing, asking for a review, unprompted contact, calling, emailing, discounts, credits
and closing a ticket — judged for this customer and **the calling key's agent profile**,
with nothing recorded and no approval filed. Actions refused for the same reason are one
caution; approval policy that applies to every customer is left out. **`next_step`** is the
most urgent recommendation no caution forbids; any more urgent one a caution rules out is in
`set_aside` with the reason.

Numbers are whole for every reader — a key without clearance is told how many problems are
open — while words from memories it may not read are left out, their ids leave `evidence`,
and `withheld` / `withheld_facts` say how much. Needs `memory:read`.

## Freshness and drift

A memory can be valid and still out of date. Every memory has a **freshness** state and an
**effective confidence**, computed when read from its own evidence — the later of the last
time the customer said it and the last time a person confirmed it:

| State | Means |
| --- | --- |
| `active` | recently evidenced |
| `aging` | past half its type's window with nothing new |
| `stale` | past its window with nothing new |
| `outdated` | evidence since points the other way (an open drift flag) |
| `conflicted` | contradicted after its last support by a statement judged weaker, and not confirmed since |
| `expired` / `superseded` | no longer standing |

Windows are per memory type (problems 30 days, intents 45, behaviour 60, goals and feedback
90, preferences 180, facts, subscriptions and relationships 365) and set in the project's
`freshness_days`. Effective confidence halves every window without evidence and never falls
below 15% of the stored confidence. Memory lists, memory detail, the agent context (each item
carries `freshness`, `effective_confidence` and a `freshness_note`, and the prompt text marks
it: `The customer prefers email. (possibly outdated: …)`), the brief and the quality report
all carry it.

### `GET /v1/customers/{customer_id}/freshness`

Counts by state, the memories that are not fresh — most concerning first, each with its
reasons — and the open drift flags. `?limit=` (default 50). Needs `memory:read`.

### Drift flags

**Drift** is evidence since a memory was stated that points the other way. Four detectors,
each reading only records authoritative for its question:

| Kind | Flags when | Reads |
| --- | --- | --- |
| `channel` | a stated channel preference, when enough of the customer's contacts since came through another channel (`drift_min_contacts`, default 5, and `drift_min_share`, default 60%) | inbound events (`whatsapp_message`, `email_received`, a `channel`/`via` field, Intercom as chat…) and agent sessions — never what you sent |
| `plan` | the remembered plan, when the latest billing events name another (`drift_min_billing_events`, default 2) | billing events with `data.plan` — which create no memory of their own |
| `usage` | a feature they said they use, unused for `drift_quiet_days.usage` (60) while the customer stayed active | feature events |
| `quiet_problem` | an open problem not reported for `drift_quiet_days.problem` (30) while the customer stayed active (`drift_min_activity`, default 5 events) | the event stream |

`channel` and `plan` are checked as events are processed; all four nightly and on demand. A
flag is **never a change**: the memory stands until a person confirms or dismisses it.

| Endpoint | |
| --- | --- |
| `GET /v1/drift?status=open&kind=&customer_id=` | flags, newest first; `status` is `open` (default), `confirmed`, `dismissed`, `cleared` or `all`. A flag on a memory this key may not read is not listed; `withheld` counts them |
| `GET /v1/drift/{id}` | one flag: `stated`, `observed`, `summary`, `counts`, `evidence` (event and session ids), the memory and the customer |
| `POST /v1/drift/{id}/confirm` `{ "note"?: … }` | write the change: a new preference ("The customer prefers WhatsApp."), plan ("upgraded from the Pro plan to the Enterprise plan"), habit ("has not used the Campaign Builder feature for 2 months") or the problem resolved — the old memory superseded (a plan *transition* is history and stays), with versions, audit and webhooks. Lifecycle and snapshots refresh at once |
| `POST /v1/drift/{id}/dismiss` `{ "note"?: … }` | keep the memory; only evidence newer than the dismissal can raise it again |
| `POST /v1/customers/{id}/drift/refresh` | run every detector for one customer now |

Confirming or rejecting the memory itself through feedback settles its flags (confirming it
is new evidence and dismisses them; rejecting or correcting clears them). A flag whose
evidence no longer holds — the customer restated the preference, the problem was reported
again — is `cleared` by the system. Reads need `memory:read`; confirm, dismiss and refresh
need `memory:write`.

Facts rules can read: `memories.stale_count`, `memories.attention_count`,
`memories.stale_share`, `drift.open_count`, `drift.kinds`, `preferences.channel_outdated`,
`preferences.observed_channel`, `preferences.observed_share`. The `channel_preference`
guardrail still follows the stated preference — never silently changed — and says when the
customer's own behaviour disagrees: *"The customer prefers email, not whatsapp — though 86% of
their contacts since came through WhatsApp; a person can confirm the change."*

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
| `GET /v1/customers/{customer_id}/state` | current state on every track, the transition that caused it, its evaluation and its `reasons` |
| `GET /v1/customers/{customer_id}/state/history?track=` | every stay in a state, newest first; `track=all` interleaves every track |
| `PUT /v1/customers/{customer_id}/state` | `{state, track?, pin?, pin_days?, note?}` — set by hand; pinned by default |
| `DELETE /v1/customers/{customer_id}/state/pin?track=` | hand the customer back to the machine on a track |
| `POST /v1/customers/{customer_id}/state/refresh` | re-evaluate every track now |
| `GET /v1/lifecycle` | the machines — the primary lifecycle and every track — and how many customers are in each state |
| `GET /v1/lifecycle/templates` | the shipped engagement and commercial tracks, ready to add |
| `GET /v1/lifecycle/customers?state=at_risk&track=` | who is in a state on a track |

The primary machine is the `lifecycle` project setting — states, an initial state and
ordered transitions whose `when` is a condition. The first matching transition wins.
`lifecycle_tracks` adds named machines beside it (new projects start with **engagement**:
new → activated → adopting → power user → at risk → churned, and **commercial**: trial →
paying → expanding → renewing → churned). Each track's state is a fact every rule can
read — `lifecycle.engagement == "at_risk"`, `lifecycle.commercial.days_in_state > 60`.

Every state carries `reasons`: the decisive clauses of the transition in words — "3
unresolved problems", "activity down 47%", "negative feedback increasing" — rendered from
the evaluation as the caller may see it, so a caller without clearance gets reasons without
the values that were withheld. The `customer.state_changed` webhook carries `track` and
`reasons` too. Writes need `customers:write`.

## Snapshots

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/customers/{customer_id}/snapshots` | every material change, newest first, each with what changed |
| `GET /v1/customers/{customer_id}/snapshots/at?time=` | what was known at a moment in the past |
| `GET /v1/customers/{customer_id}/snapshots/{snapshot_id}` | one snapshot with its full fact document |

A snapshot is taken only when something material changes — a band, a count, the state, the
plan — so each one is a moment worth knowing about. Needs `memory:read`.

## What changed

### `GET /v1/customers/{customer_id}/changes`

"Tell me what changed since I last spoke to them", as typed changes with before, after, when
and the evidence — and the customer *then* and *now*.

| Parameter | Meaning |
| --- | --- |
| `since` | a span (`7d`, `12h`, `2w`, `3mo`), an ISO time or date, a snapshot id, `last_session` (the last finished conversation with the customer) or `last_run` (the last time an agent acted for them). Default `30d`. |
| `until` | an ISO time, a span back from now, or a snapshot id. Default now. |
| `agent` | with `last_session`/`last_run`: only that agent's |
| `types` | comma-separated: `subscription, lifecycle, health, risk, trajectory, problem, intent, preference, goal, feedback, relationship, fact, memory, signal, activity` |
| `order` | `time` (newest first, default) or `importance` |
| `limit` | 1–200, default 50 |

```json
{
  "customer_id": "acme",
  "window": { "since": "…", "until": "…", "basis": "last_session", "found": true, "label": "Since the last conversation (17 Sep 2026)" },
  "summary": "Since the last conversation (17 Sep 2026): upgraded from Starter to Pro; a new problem; a problem resolved; said they may cancel; health moved from healthy to at risk.",
  "changes": [{
    "type": "subscription", "kind": "changed", "title": "Upgraded from Starter to Pro",
    "before": "The customer is on the Starter plan.", "after": "The customer upgraded from the Starter plan to the Pro plan.",
    "detected_at": "…", "evidence": ["mem_…", "mem_…"], "source": "memory",
    "detail": { "plan": "pro", "previous_plan": "starter", "direction": "upgraded" }, "importance": 0.95
  }, {
    "type": "lifecycle", "kind": "moved", "title": "Engagement: adopting → at risk", "track": "engagement",
    "reasons": ["3 unresolved problems", "activity down 47%"]
  }],
  "counts": { "subscription": 1, "problem": 2, "lifecycle": 1 },
  "withheld": 0,
  "then": { "snapshot_id": "snp_…", "state": { "plan": "starter", "health_band": "healthy", "tracks": { "engagement": "adopting" } }, "description": "healthy (82) · Starter plan · 0 open problems" },
  "now":  { "live": true, "state": { "plan": "pro", "health_band": "at_risk" }, "description": "at risk (54) · Pro plan · 2 open problems" }
}
```

Each change is read from the record that is authoritative for it: memories first seen in the
window (problems opened and resolved, plan and preference changes with what they replaced,
intents, strong feedback, facts and relationships), memory versions (a problem reported
again, a person's correction), goal evidence, lifecycle stays on every track, the snapshots
at each end (health bands, churn risk, trajectory), signals that started or stopped, and
activity against the equal window before — compared only when the customer existed for all
of it. A caller without clearance is not shown changes about memories they may not read;
`withheld` counts them, and a *before* quoting a hidden memory reads `[withheld]`. When
`last_session`/`last_run` has nothing to go back to, the window falls back to 30 days and
`window.note` says so. Needs `memory:read`.

### `GET /v1/customers/{customer_id}/compare?from=&to=`

The customer at two moments side by side — plan, health, lifecycle and every track,
problems, goals, channel, intents, signals — and every fact that differs, with `added` and
`removed` for lists. `from`/`to` take the same forms as `until`; `to` defaults to now.

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

## Memory evaluation

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/evals` / `POST /v1/evals` | list sets (with the latest run), create one |
| `GET /v1/evals/{set_id}` / `DELETE` | a set with its cases and runs |
| `POST /v1/evals/{set_id}/cases` | up to 200 cases: questions or events (below) |
| `DELETE /v1/evals/{set_id}/cases/{case_id}` | remove a case |
| `POST /v1/evals/{set_id}/runs` | `{label?, k?, wait?}` — run the set; inline up to 100 cases |
| `GET /v1/evals/{set_id}/runs` | runs, newest first |
| `GET /v1/evals/runs/{run_id}` | a run with per-case results and the comparison |
| `POST /v1/evals/{set_id}/regression` | `{settings, k?}` — the set as configured and under proposed settings |
| `GET /v1/evals/scorecard` | memory quality in one place |
| `GET /v1/evals/suggestions` | recent real questions and what came back — pick the right answer to make a case |

Two kinds of case. A **retrieval** case is a question with its right answer:
`{customer_id, question, expected_memory_ids?, expected_phrases?}`. An **extraction** case is
an event with the memories it should — and must not — become, run through the real pipeline
as a dry run (the same one as `/v1/events/preview`):

```json
{ "customer_id": "acme", "kind": "extraction", "question": "A fix closes the problem",
  "event": { "event_type": "support_message", "data": { "message": "The payroll export works again, thanks." } },
  "expect": [ { "contains": "payroll export", "action": "conflict" } ],
  "forbid": [ { "type": "problem", "action": "create" } ] }
```

An expectation names any of `type`, `contains` (words, matched in any form), `entity`,
`sensitivity` and `action` (what consolidation does: `create`, `merge`, `update`,
`conflict`, `ignore`); `"expect_nothing": true` says the event should become no memory.
Unknown fields are refused, so a typo cannot silently weaken a case.

A run reports Recall@k, Hit@k, MRR and citation hit rate for questions, and for events
`metrics.extraction`: `accuracy` (cases passing), `expected_recall`, `false_memory_rate`
(cases that made a forbidden memory, or any memory when nothing was expected), and — for
statements that were extracted at all — `type_accuracy`, `sensitivity_accuracy` and
`consolidation_accuracy`. A failed expectation says why: `"near_miss": "typed fact, expected
preference"`. **`comparison.regressed` is true when any case that used to pass no longer
does** — the flag to fail a CI job on.

A **regression** runs the set twice: as configured, and under `settings` validated exactly as
a save would be and never saved. `newly_failing` names every case the change would break,
`safe` is false when there is one, and the proposed run (`overrides`, `proposed: true`) is
never the next run's baseline. The **scorecard** weights the latest run of every set by the
cases it scored and adds the quality report's duplicate control, consistency and freshness.
Runs never write to the query log or count as usage. Writes need `memory:write`.

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
`amount`, `topic`, `plan`, `reply` (true when answering the customer's own message), or
anything else (`request.<key>` in a project rule). `"dry_run": true` decides without
recording or filing an approval. To act, prefer the gateway below: it decides the same way
and records the action.

Built-in rules (switch any off in the `guardrails` setting):

| Rule | Decision |
| --- | --- |
| `open_problem_blocks_selling` | deny selling with an open problem |
| `at_risk_blocks_selling` | deny selling to an at-risk customer |
| `churn_intent_blocks_promotion` | deny marketing to a customer who said they may leave |
| `channel_preference` | deny contact on a channel the customer does not prefer |
| `respect_opt_out` | deny what the customer asked not to receive: calls, emails, texts, WhatsApp (by action or `request.channel`), selling after "no sales", marketing after "unsubscribe", any outreach after "do not contact" — except a `reply` to their own message |
| `unresolved_problem_blocks_closing` | deny closing a ticket whose problem is open; approval when the topic is not given |
| `money_requires_approval` | discounts, credits, refunds, waived fees |
| `account_change_requires_approval` | cancel, downgrade, change plan, pause, delete |

Project rules, in the `guardrails` setting:

```json
{ "disabled": [], "approval_ttl_hours": 24,
  "rules": [ { "name": "large_discounts", "actions": ["offer_discount"],
               "when": "request.amount > 200 and health.band == \"critical\"",
               "decision": "deny", "message": "No discounts over 200 for critical accounts without the CS lead." },
             { "name": "second_credit", "actions": ["issue_credit"],
               "when": "actions.issue_credit.count_30d >= 1",
               "decision": "require_approval", "message": "A second credit this month needs a person." } ],
  "auto_approve": [ { "actions": ["process_refund"], "up_to": 50, "max_per_30_days": 3 } ] }
```

`auto_approve` lifts the built-in `money_requires_approval` / `account_change_requires_approval`
for the listed actions — within `up_to` (required for money actions) and while the customer's
history is under `max_per_30_days`. A project rule that requires approval is more specific
than a limit and is never lifted by one. Rules read the customer's **action history** as
facts — `actions.<action or family>.count_7d | count_30d | amount_30d | days_since_last`
(families: `selling`, `promotion`, `contact`, `closing`, `money`, `account`, `support`) —
counted from actions the gateway allowed or agents reported done; with no history, counts
and amounts read 0. Conditions are compiled on save (`422` naming the fact you meant). `GET /v1/agent/checks`
lists recorded checks (`customer_id`, `decision`, `agent`, `session_id`);
`GET /v1/agent/guardrails` returns the recognised actions and built-in rules.

## Action gateway

The one call an agent makes before it acts — decided like `/check`, and recorded as an
action whose outcome becomes the customer's history.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/agent/actions/request` | `{customer_id, action, request, idempotency_key?, session_id?, approval_id?}` → `201` with the action |
| `GET /v1/agent/actions/{id}` | where it stands, and `next_step` |
| `POST /v1/agent/actions/{id}/proceed` | after a person approved: the rules run again on today's facts and the approval is redeemed |
| `POST /v1/agent/actions/{id}/complete` | `{"outcome": "done" \| "failed" \| "cancelled", "note"?, "external_ref"?}` |
| `GET /v1/agent/actions` | filter by `customer_id`, `action`, `status`, `agent` |

```json
{ "id": "act_…", "action": "process_refund", "request": { "amount": 25 },
  "status": "allowed", "decision": "allow",
  "summary": "Approved automatically: a refund of 25 within the limit of 50 (1 of 3 this month), set by the project.",
  "next_step": "Go ahead, then report the outcome with /complete.",
  "reasons": [ … ], "approval": null, "check_id": "chk_…" }
```

`status` is `allowed` (go ahead, then `/complete`), `pending_approval` (a person was asked —
don't act; `/proceed` once they decide), `denied`, and after that `done`, `failed`,
`cancelled` (a waiting action can be abandoned) or `expired` (its approval lapsed). A person's
*no* settles a waiting action to `denied` at once; a *yes* still needs `/proceed`, because
the rules run again then. The same `idempotency_key` returns the same action (a different
request under it is a `409`). Completing emits `agent.action_completed`. Merging customers
moves their action history with them.

## Approvals

A `require_approval` check returns `"approval": {"id": "apr_…", "status": "pending", …}`.
Retrying the same request returns the same approval.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/agent/approvals?status=pending` | the queue |
| `GET /v1/agent/approvals/{id}` | poll while waiting, or subscribe to `agent.approval_decided` |
| `POST /v1/agent/approvals/{id}/decision` | `{"decision": "approve" \| "reject", "note": "…"}` — needs `approvals:decide` |

Each approval carries what a reviewer needs to decide: `evidence_memories` — the memories its
reasons cite, in their own words, as the reviewer may read them (`withheld_evidence` counts
the rest) — `customer` (health, plan, lifecycle, open problems as last recorded) and
`action_id`, the gateway action waiting on it.

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
| `GET /v1/agent/runs/{id}/trace` | what it was given, what it was **not** given and why, and what it decided |

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

### `GET /v1/agent/runs/{id}/trace`

"Why did my agent do this?" — including the memories it never saw.

```json
{
  "question": "Is the Shopify sync still failing?",
  "decision": { "kind": "answer", "strategy": "status:latest", "confidence": 0.85, "reasoning": ["…"] },
  "given": [
    { "id": "mem_…", "rank": 1, "verdict": "cited", "why": "#1, cited by the answer — found by keyword, semantic, score 0.80.",
      "content_then": "The Shopify sync works now.", "content_now": "The Shopify sync works now." },
    { "id": "mem_…", "rank": 2, "verdict": "not_cited", "why": "#2, retrieved but not cited — the answer drew on other evidence (…)." }
  ],
  "ignored": [
    { "id": "mem_…", "reason": "superseded", "content": "The Shopify sync fails during checkout.",
      "why": "Superseded on 12 Sep 2026 by a newer memory: “The Shopify sync works now.” — which the agent was given (#1)." },
    { "id": "mem_…", "reason": "type_cap", "position": 6, "why": "Ranked #6 (score 0.40), but 5 problem memories were already included." },
    { "id": "mem_…", "reason": "withheld_profile", "visible": false, "content": "[withheld]",
      "why": "The support-agent profile does not read feedback memories." }
  ],
  "cut": { "candidates": 14, "limit": 10, "per_type": 5 },
  "recorded": true
}
```

`verdict` is `cited`, `given` (in a briefing) or `not_cited`. `reason` is one of
`below_cut`, `type_cap`, `token_budget`, `section_cap`, `duplicate`, `superseded`,
`expired`, `withheld_restricted`, `withheld_profile`. Ranking reasons are recorded as
retrieval ranks; superseded, expired and withheld memories that matched the question are
found when the run is recorded and kept as ids. A reader who may not see one of them gets
`[withheld]` and the reason — and an answer or reasoning drawn from one is withheld too.
Runs recorded before this existed have `recorded: false`.

## MCP

The Python SDK ships an MCP server: `pip install ai-memory`, then

```bash
MEMORA_API_KEY=mk_live_… MEMORA_BASE_URL=https://memory.internal ai-memory-mcp          # stdio
ai-memory-mcp --transport http --port 8765                                             # Streamable HTTP
```

Tools: `ask_memory`, `search_memory`, `customer_360`, `customer_brief`, `customer_changes`,
`customer_timeline`, `get_health`, `get_goals`, `get_recommendations`, `check_action`,
`request_action`, `proceed_action`, `report_action`, `explain_answer`, `remember`. Over HTTP each caller sends their own key as
`Authorization: Bearer mk_…`; the key's scopes, clearance and profile apply to every tool.
`customer_brief` returns the [brief](#customer-brief)'s Markdown page (with the JSON as
structured content) and takes `since` and `agent`.

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
| `agent.action_completed` | An agent reported an action from the gateway done, failed or cancelled |
| `agent.approval_requested` | An agent needs a person to approve an action |
| `agent.approval_decided` | A request was approved, rejected — or lapsed (`status: "expired"`) |
| `memory.drift_detected` | Evidence says a standing memory may be out of date — a changed channel, plan or habit, a problem gone quiet ([drift](#freshness-and-drift)) |
| `memory.drift_resolved` | A drift flag was confirmed (the memory was changed), dismissed, or cleared because the evidence no longer points the other way |

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
