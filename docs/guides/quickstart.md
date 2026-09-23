# Quickstart

## 1. Requirements

* Python 3.11+
* PostgreSQL 14+ with the `vector` extension
* Redis 6+
* Node 18+ (dashboard and SDK)

## 2. Configure

```bash
cp .env.example .env
```

There is nothing to configure for the language engine: extraction, consolidation,
retrieval and answering are deterministic and run in-process. No API key, no provider, no
network call.

Two values do need setting before production, and `check_production_safety` refuses to
start without them: `JWT_SECRET` / `API_KEY_SECRET`, and `SECRETS_ENCRYPTION_KEY`, which
encrypts webhook and integration signing secrets at rest. Generate the last one with:

```bash
python -c "from common.crypto import generate_key; print(generate_key())"
```

The one deploy-time decision is vector width:

```env
EMBEDDING_DIMENSIONS=1536
```

It defines the pgvector column, so changing it later means a migration and a re-embed
(the `generate_embeddings` worker job). 512 is plenty for most workloads; 1536 gives more
headroom against hash collisions on large corpora.

## 3. Install and migrate

```bash
make install
createdb memory && psql memory -c 'CREATE EXTENSION vector'
make migrate
```

## 4. Run

```bash
make api      # http://localhost:8000/docs
make worker
make web      # http://localhost:3000
```

Verify configuration and connectivity at any time:

```bash
make check
```

## 5. Seed the demo story

```bash
make seed
```

This creates the README's example — John upgrades, connects Shopify, hits repeated
failures, contacts support twice, downgrades — processes it, and asks *"Why did this
customer downgrade?"*. It prints a dashboard login and a project API key.

## 6. Send your own event

```bash
curl -X POST http://localhost:8000/v1/events \
  -H "X-API-Key: $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cus_123",
    "event_type": "support_message",
    "external_event_id": "msg_1",
    "data": { "message": "I have tried connecting Shopify three times but it still does not work." }
  }'
```

Then ask about it:

```bash
curl -X POST http://localhost:8000/v1/memory/query \
  -H "X-API-Key: $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "customer_id": "cus_123", "query": "What problems has this customer experienced?" }'
```
