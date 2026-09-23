# Operating the system

## Health

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | liveness; never touches the database |
| `GET /ready` | readiness; checks PostgreSQL and the queue |
| `GET /metrics` | Prometheus exposition |

The API refuses to start in `APP_ENV=production` with development secrets, a wildcard CORS
origin, or a missing AI key. That check is in `Settings.check_production_safety()`.

## What to alert on

| Metric | Why |
| --- | --- |
| `queue_depth` | the worker is falling behind |
| `events_processed_total{status="failed"}` | extraction or provider failures |
| `event_processing_duration_seconds` | pipeline latency (CPU-bound: extraction + embedding) |
| `memories_created_total` / `memories_updated_total` | whether consolidation is merging or duplicating |
| webhook deliveries in `failed` status | a customer's receiver is down and alerts are being lost |
| `retrieval_duration_seconds{strategy}` | which retrieval leg is slow |
| `api_request_duration_seconds` | ingestion latency (should stay flat) |
| `keys.rotation_due` log events | the secrets key is overdue, or a rotation was started and never finished |
| `email.failed` log events | a mail relay is rejecting messages, so invitations are not arriving |

## Scheduled jobs

| Job | Schedule | Effect |
| --- | --- | --- |
| `deliver_webhooks` | every 30 s | drains due outbound deliveries, with backoff on failure |
| `sweep_pending_events` | every 15 min | re-queues events stuck `pending`/`processing` |
| `apply_retention` | 03:15 daily | expires due memories, enforces retention windows |
| `purge_old_deliveries` | 03:45 daily | trims successful webhook history after 30 days |
| `summarize_project_all` | 04:00 daily | refreshes rolling customer summaries |
| `snapshot_signals_all` | 04:30 daily | records each customer's forecast, prunes snapshots past 120 days |
| `sweep_stale_goals` | 05:00 daily | marks goals with no evidence for 30 days as stalled |
| `close_idle_sessions` | :10 and :40 | closes agent sessions idle for 12 h, writing their summary memory |
| `mine_vocabulary_all` | 05:30 daily | re-mines each project's learned synonym table |
| `check_key_rotation` | 06:00 daily | warns when the secrets key is overdue, or a rotation was abandoned |
| `report_queue_depth` | every 5 min | updates the queue gauge |

The last three matter for customers who have gone quiet. Signals and goals are refreshed on
the event path, which never fires again for somebody who has stopped sending events — and
that is exactly the population a churn forecast is about.

## Manual jobs

```python
await queue.enqueue_job("reprocess_customer", project_id, customer_id)   # replay history
await queue.enqueue_job("refresh_customer_foresight", project_id, customer_id)  # goals + signals
await queue.enqueue_job("mine_vocabulary", project_id)                  # relearn the vocabulary
await queue.enqueue_job("retry_failed_events", project_id)               # after an outage
await queue.enqueue_job("generate_embeddings", project_id)               # after a width change
await queue.enqueue_job("consolidate_customer_memories", project_id, customer_id)
await queue.enqueue_job("summarize_project", project_id)                 # refresh rolling summaries
await queue.enqueue_job("link_project", project_id)                      # rebuild causal links
await queue.enqueue_job("warm_context", project_id, customer_id)         # pre-build agent context
await queue.enqueue_job("reclassify_memories", project_id)               # re-apply restriction rules
await queue.enqueue_job("check_key_rotation")                            # key age + unfinished rotation
```

The dashboard exposes the two that operators need most: **Retry** on any failed event and
**Retry failed** for the whole backlog (Events screen).

Replaying is always safe: events are immutable and consolidation is idempotent per event id.

## "I sent an event and nothing happened"

The commonest support question, and it is self-serve.

**Before sending**, ask:

```bash
curl -X POST $API/v1/events/preview -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"cus_1","event_type":"support_message",
       "data":{"message":"The sync keeps failing."}}'
```

Nothing is written. `stop_reason` is the field to read: when it is set, nothing else
happened and it says why — usually that the event's importance was below the project's
threshold, with both numbers named.

**After sending**, the same explanation is on the event:

```bash
curl $API/v1/events/$EVENT_ID -H "X-API-Key: $KEY" | jq .outcome
```

The Event explorer shows it as a **Result** column, and the full decision trace when a row
is opened. A row reading `—` is an event processed before outcomes were recorded; retrying
it fills it in.

The four things that usually turn out to be the cause:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `stop_reason` names the threshold | The event type scores below `min_event_importance` | Lower the threshold, or give that type an `event_importance` override |
| `stop_reason` says no readable values | The payload was empty or held only `importance`, `metadata`, `_meta` | Put the text in any other field; every scalar is read as `key: value` |
| Memory created where you expected a merge | The nearest existing memory was below `consolidation_similarity` | The preview reports the actual similarity and the closest memory — tune against that number |
| The memory says less than the customer wrote | PII redaction removed part of it | `redactions` names what was removed, by kind and count |

## Changing the vector width

1. Set `EMBEDDING_DIMENSIONS`.
2. Generate a migration altering `embeddings.embedding` and recreate the HNSW index.
3. Run `generate_embeddings` per project.

Until step 3 completes, semantic search only sees re-embedded memories; keyword, temporal,
type and entity retrieval keep working, so answers degrade rather than disappear.

Vectors are produced by a pure function of the text, so re-embedding is deterministic and
can be re-run safely at any time.

## Tracing an answer

Every AI answer writes a `query_logs` row containing the query, the answer, the memory ids,
the source event ids, the engine version, the answer strategy and latency. `GET
/v1/projects/{project_id}/queries` reads it back, which is how you audit "why did the agent
say that?" after the fact.
