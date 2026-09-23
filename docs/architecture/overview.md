# Architecture

## The shape of the system

```
Customer SaaS ──POST /v1/events──► API (FastAPI)
                                     │  validate → deduplicate → store → enqueue → 202
                                     ▼
                                 Redis queue
                                     │
                                     ▼
                              Memory worker (ARQ)
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
     entity extraction         memory extraction            embedding
            └─────────────────────────┼─────────────────────────┘
                                      ▼
                            memory consolidation
                                      ▼
                        PostgreSQL + pgvector (single store)
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
            structured queries                    vector search
                    └─────────────────┬─────────────────┘
                                      ▼
                             retrieval engine → ranking
                                      ▼
                               context builder
                                      ▼
                       answer with evidence
```

## Layers

| Layer | Package | Responsibility |
| --- | --- | --- |
| Shared primitives | `packages/common` | settings, errors, logging, metrics, PII, text, time |
| Persistence | `packages/database` | models, repositories, migrations. **All SQL lives here.** |
| Language engine | `packages/nlp` | lexicons, tokenisation, classification, entities, embeddings, answers |
| Integrations | `packages/integrations` | inbound connectors: signature verification and normalisation |
| Webhooks | `packages/webhooks` | outbound events: signing, payload envelopes, dispatch |
| Domain | `packages/memory_engine` | extraction, consolidation, temporal behaviour, retrieval, ranking, context |
| HTTP | `apps/api` | authentication, schemas, services, routers |
| Background | `apps/worker` | the pipeline, backfills, retention, sweeps |
| Dashboard | `apps/web` | Next.js explorer, graph and playground |
| Client | `sdk/typescript` | `@ai-memory/sdk` |

The memory engine depends on the AI *interfaces*, never on a specific provider, and the
API depends on the engine rather than reimplementing any of it. The worker and the API
run the exact same `MemoryEngine`.

## The write path is deliberately small

`POST /v1/events` authenticates, validates, checks idempotency, writes an immutable row,
enqueues a job and returns `202`. Everything that costs CPU — extraction, consolidation,
embedding — happens in the worker, so ingestion latency stays flat under load and a slow
queue is a processing delay rather than an ingestion failure.

If Redis is unavailable at ingest time, the event still lands in PostgreSQL with status
`pending`; the worker's 15-minute sweep re-queues it. Nothing is lost.

## Cost control

Understanding every event is pointless: a page view carries no durable meaning.
`memory_engine.extraction.filters` scores each event from its type and payload; events
below the project's threshold are marked `skipped` and never reach extraction. Free-text
fields raise the score, because a human writing a sentence is nearly always worth
remembering.

Because understanding is deterministic and local, the marginal cost of an event is CPU
only — no tokens, no provider quota, no rate limit, and no per-event spend to forecast.

## Why one database

PostgreSQL holds the events, memories, graph and vectors. That means one transaction can
create a memory, its version row, its embedding and its entity links — so a partial
failure cannot leave a memory without provenance. Splitting vectors into a dedicated store
would buy scale the product does not need yet and cost exactly the consistency it does.

## Consistency rules that the code enforces

1. **Events are immutable.** Processing writes status metadata; the payload is never edited.
2. **Memories never change silently.** Every update writes a `memory_versions` row.
3. **Conflicts supersede, they do not delete.** The losing memory keeps its content with
   `status = superseded` and a pointer to its replacement.
4. **Every query is project-scoped.** Repositories take `project_id` on every read.
5. **Inference is labelled.** Answers cite memory ids, memories cite event ids.
