# 🧠 AI Memory Layer for SaaS

> **Give every SaaS product a persistent, intelligent memory of its users.**

AI Memory Layer is an infrastructure platform that transforms fragmented customer events, conversations, product activity, billing events, preferences, and interactions into a continuously evolving **User Memory Graph**.

Instead of forcing every SaaS company to build its own customer-memory infrastructure, developers can send events to one API and retrieve intelligent, contextual memories whenever an AI agent needs to understand a customer.

---

## 🚀 Vision

Modern SaaS applications know what users **did**, but often don't understand what those actions **mean**.

A customer may:

```text
Use Feature A
      ↓
Encounter an error
      ↓
Contact support
      ↓
Try another solution
      ↓
Talk to sales
      ↓
Upgrade
      ↓
Downgrade
```

This information is usually distributed across:

* Product analytics
* CRM
* Support systems
* Billing systems
* Emails
* Chat
* Databases
* Logs
* AI conversations

AI Memory Layer turns these fragmented events into a unified customer memory.

### The goal

```text
Raw Events
    ↓
Understanding
    ↓
Memories
    ↓
Relationships
    ↓
Timeline
    ↓
Relevant Context
    ↓
AI Reasoning
```

---

# 🎯 Core Problem

Most AI applications have short-term context.

A typical AI agent sees:

```text
Current conversation
        ↓
Agent
        ↓
Response
```

AI Memory Layer provides:

```text
Current conversation
        +
Customer history
        +
Preferences
        +
Past problems
        +
Product usage
        +
Relationships
        +
Important events
        +
Temporal context
        ↓
AI Memory Layer
        ↓
Relevant Customer Context
        ↓
Your AI agent
```

This allows AI agents to understand customers across sessions and systems.

---

# 💡 Example

Suppose a SaaS customer named John has this history:

```text
Aug 01
Upgraded to Pro

Aug 05
Connected Shopify

Aug 08
Shopify integration failed

Aug 09
Contacted support

Aug 11
Contacted support again

Aug 12
Downgraded to Starter
```

The system shouldn't simply store six independent events.

It should understand:

```text
Memory:

John has experienced repeated Shopify integration problems.

Related memories:

- Uses Shopify
- Contacted support twice
- Previously upgraded to Pro
- Recently downgraded
```

An AI agent can then ask:

> Why did John downgrade?

The Memory Layer retrieves the relevant history and produces contextual evidence.

---

# 🏗️ Product Architecture

```text
                         CUSTOMER SAAS
                              │
                              │ Events
                              ▼
                    ┌───────────────────┐
                    │    API Gateway    │
                    │      FastAPI      │
                    └─────────┬─────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │   Event Pipeline  │
                    │                   │
                    │ Validation        │
                    │ Normalization     │
                    │ Deduplication     │
                    └─────────┬─────────┘
                              │
                              ▼
                         Redis Queue
                              │
                              ▼
                    ┌───────────────────┐
                    │   Memory Worker   │
                    └─────────┬─────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
       Entity Extraction  Memory Extraction  Embedding
              │               │                │
              └───────────────┼────────────────┘
                              ▼
                    Memory Consolidation
                              │
                              ▼
                    ┌───────────────────┐
                    │    PostgreSQL     │
                    │     pgvector      │
                    └─────────┬─────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             Structured Data       Vector Search
                    │                   │
                    └─────────┬─────────┘
                              ▼
                       Retrieval Engine
                              │
                              ▼
                       Context Builder
                              │
                              ▼
                       Answer Composer
                              │
                              ▼
                     Answer with Evidence
```

---

# 🧰 Technology Stack

## Frontend

* Next.js
* TypeScript
* Tailwind CSS
* shadcn/ui
* TanStack Query
* Recharts
* React Flow

## Backend

* Python
* FastAPI
* Pydantic
* SQLAlchemy
* Alembic

## Database

* PostgreSQL
* pgvector

## Queue / Background Processing

* Redis
* ARQ or Celery

Start with ARQ for a lightweight implementation. Evaluate Celery later if task orchestration becomes more complex.

## Language engine

No LLM. Understanding is deterministic and runs in-process:

* Rule-based sentence classification (cue lexicons, negation, sentiment)
* Template extraction for structured events
* Gazetteer and pattern entity recognition
* Local lexical embeddings (hashed n-grams, no model files)
* Extractive summarisation and template-composed answers

The same history always produces the same memory and the same answer, at zero marginal
cost, and no customer text ever leaves the deployment.

## Embeddings

Deterministic local embeddings: lemmatised word unigrams and bigrams plus character
n-grams, signed-hashed into a fixed-width vector and L2 normalised.

Vector width is configurable through `EMBEDDING_DIMENSIONS`; it defines the pgvector
column, so changing it requires a migration and a re-embed.

## Authentication

* JWT/session authentication
* API keys for customer applications
* Role-based access control

---

# 📁 Project Structure

Use a single monorepo.

```text
ai-memory-layer/
│
├── apps/
│   │
│   ├── web/
│   │   ├── app/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── lib/
│   │   └── public/
│   │
│   ├── api/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   │
│   │   │   ├── api/
│   │   │   │   ├── auth.py
│   │   │   │   ├── events.py
│   │   │   │   ├── customers.py
│   │   │   │   ├── memories.py
│   │   │   │   ├── query.py
│   │   │   │   ├── context.py
│   │   │   │   ├── projects.py
│   │   │   │   └── usage.py
│   │   │   │
│   │   │   ├── core/
│   │   │   │   ├── config.py
│   │   │   │   ├── security.py
│   │   │   │   └── logging.py
│   │   │   │
│   │   │   ├── models/
│   │   │   ├── schemas/
│   │   │   ├── repositories/
│   │   │   └── services/
│   │   │
│   │   └── tests/
│   │
│   └── worker/
│       ├── tasks/
│       │   ├── process_event.py
│       │   ├── extract_entities.py
│       │   ├── extract_memory.py
│       │   ├── consolidate_memory.py
│       │   ├── generate_embedding.py
│       │   └── build_context.py
│       │
│       └── main.py
│
├── packages/
│   │
│   ├── memory_engine/
│   │   ├── extraction/
│   │   ├── consolidation/
│   │   ├── retrieval/
│   │   ├── ranking/
│   │   ├── temporal/
│   │   └── context/
│   │
│   ├── ai/
│   │   ├── providers/
│   │   ├── embeddings/
│   │   ├── prompts/
│   │   └── structured_output/
│   │
│   ├── database/
│   │   ├── models/
│   │   ├── repositories/
│   │   └── migrations/
│   │
│   └── common/
│
├── sdk/
│   └── typescript/
│
├── docs/
│   ├── architecture/
│   ├── api/
│   └── guides/
│
├── scripts/
│
├── tests/
│
├── .env.example
├── pyproject.toml
├── package.json
└── README.md
```

---

# 🧠 Memory Model

The system must distinguish between:

## 1. Events

Immutable things that happened.

Example:

```json
{
  "type": "feature_used",
  "customer_id": "cus_123",
  "data": {
    "feature": "shopify_integration"
  }
}
```

Events should never be silently modified.

---

# 2. Memories

Facts or conclusions derived from events.

Example:

```json
{
  "type": "problem",
  "content": "Customer is experiencing Shopify integration problems.",
  "importance": 0.92,
  "confidence": 0.94,
  "status": "active"
}
```

Memories can evolve.

---

# 3. Entities

Objects referenced by customer activity.

Examples:

```text
Customer
Company
Product
Feature
Integration
Subscription
Support Ticket
Employee
Conversation
Campaign
Order
```

---

# 4. Relationships

Connections between entities.

Example:

```text
Customer
   │
   ├── USES → Shopify
   ├── OWNS → Restaurant
   ├── HAS_PROBLEM → Shopify Issue
   ├── CONTACTED → Support
   └── SUBSCRIBED_TO → Pro
```

---

# 🗄️ Database Schema

## organizations

```text
id
name
created_at
updated_at
```

## projects

```text
id
organization_id
name
api_key_hash
created_at
updated_at
```

## customers

```text
id
project_id
external_id
email
name
metadata
created_at
updated_at
```

Unique constraint:

```text
(project_id, external_id)
```

---

## events

```text
id
project_id
customer_id
event_type
external_event_id
data
occurred_at
created_at
processed_at
status
```

Events must be immutable.

Use `external_event_id` for idempotency.

---

## memories

```text
id
project_id
customer_id
type
content
importance
confidence
status
source
first_seen_at
last_seen_at
expires_at
created_at
updated_at
```

Memory types:

```text
fact
preference
problem
goal
behavior
relationship
subscription
feedback
intent
summary
```

---

## memory_versions

```text
id
memory_id
previous_content
new_content
reason
created_at
```

This provides an audit trail for memory evolution.

---

## entities

```text
id
project_id
type
name
external_id
metadata
created_at
updated_at
```

---

## relationships

```text
id
project_id
source_entity_id
relationship_type
target_entity_id
confidence
created_at
updated_at
```

---

## embeddings

```text
id
project_id
memory_id
embedding
model
created_at
```

The `embedding` column uses pgvector.

---

# 🔄 Event Processing Pipeline

When the API receives an event:

```text
POST /v1/events
       │
       ▼
Authentication
       │
       ▼
Schema validation
       │
       ▼
Idempotency check
       │
       ▼
Store immutable event
       │
       ▼
Queue background job
       │
       ▼
Return 202
```

The worker then:

```text
Event
 ↓
Normalize
 ↓
Extract entities
 ↓
Extract candidate memories
 ↓
Find related existing memories
 ↓
Compare
 ↓
Merge / create / update
 ↓
Calculate importance
 ↓
Generate embedding
 ↓
Update relationships
 ↓
Persist
```

---

# 🤖 Memory Extraction

Extraction is rule-based and explainable. Each sentence is classified by cue phrases,
negation and sentiment; structured events are turned into statements by templates.

Rules:

```text
Extract only durable, useful information.
Never infer facts that are not supported by the input.
Rewrite first-person text into third-person statements, or quote it verbatim.
Attach the cues that produced every classification.
```

Input:

```json
{
  "customer_id": "cus_123",
  "event_type": "support_message",
  "data": {
    "message": "I've tried connecting Shopify three times but it still doesn't work."
  }
}
```

Output:

```json
{
  "memories": [
    {
      "type": "problem",
      "content": "Customer is having difficulty connecting Shopify.",
      "importance": 0.91,
      "confidence": 0.97
    }
  ],
  "entities": [
    {
      "type": "integration",
      "name": "Shopify"
    }
  ]
}
```

The output is validated with Pydantic before entering the database, and each memory keeps
the rule and cues that produced it.

---

# 🧩 Memory Consolidation

Memory extraction alone isn't enough.

Suppose the system already contains:

```text
Customer has Shopify authentication problems.
```

A new event says:

```text
Shopify connection is still failing.
```

The system should not create:

```text
Memory #1
Memory #2
```

Instead:

```text
Existing Memory
       +
New Evidence
       ↓
Memory Consolidation
       ↓
Updated Memory
```

Result:

```text
Customer has experienced recurring Shopify integration
problems over the last two weeks.
```

The system must preserve the previous memory version.

---

# ⚖️ Memory Conflict Resolution

Example:

```text
Old:
Customer prefers email.

New:
Please contact me on WhatsApp.
```

The system should consider:

```text
recency
confidence
frequency
explicitness
source
```

and update the memory when justified.

Never silently overwrite historical information.

---

# 🧠 Memory Importance

Each memory should have an importance score.

Factors:

```text
Recency
Frequency
Explicitness
Business relevance
User impact
Relationship relevance
```

Example:

```text
Page viewed
importance = 0.05

Feature used
importance = 0.25

Support issue
importance = 0.75

Cancellation request
importance = 0.98
```

These values should be configurable.

---

# ⏳ Memory Freshness

Memories should support temporal behavior.

Each memory can have:

```text
first_seen_at
last_seen_at
expires_at
```

Some memories decay naturally.

Example:

```text
"Currently evaluating Shopify"

↓
after 90 days
↓
less relevant
```

But durable facts should not automatically disappear.

---

# 🔍 Retrieval Engine

A query should combine multiple retrieval methods.

```text
User Query
    │
    ├── Semantic Search
    │
    ├── Keyword Search
    │
    ├── Temporal Search
    │
    ├── Entity Search
    │
    └── Relationship Search
             │
             ▼
        Candidate Memories
             │
             ▼
        Ranking Engine
             │
             ▼
        Relevant Context
```

Do not rely only on vector similarity.

---

# 📊 Memory Ranking

Candidate memories should be ranked using:

```text
similarity
+
importance
+
confidence
+
recency
+
frequency
+
relationship relevance
```

Example conceptual formula:

```text
score =
    semantic_similarity * 0.35
    +
    importance * 0.20
    +
    confidence * 0.20
    +
    recency * 0.15
    +
    relationship_relevance * 0.10
```

Make these weights configurable.

---

# 🧠 Context Builder

The Context Builder converts retrieved memories into an AI-ready context.

Example:

```json
{
  "customer": {
    "id": "cus_123",
    "name": "John"
  },
  "important_facts": [
    "Uses Shopify integration",
    "Previously subscribed to Pro"
  ],
  "active_problems": [
    "Shopify integration failures"
  ],
  "recent_events": [
    "Contacted support twice",
    "Downgraded subscription"
  ],
  "preferences": [],
  "relevant_relationships": []
}
```

The context builder should enforce:

* Token limits
* Relevance
* Deduplication
* Recency
* Importance

---

# 🔌 Public API

## Send Event

```http
POST /v1/events
```

Request:

```json
{
  "customer_id": "cus_123",
  "event_type": "feature_used",
  "external_event_id": "evt_456",
  "occurred_at": "2026-09-17T10:00:00Z",
  "data": {
    "feature": "campaign_builder"
  }
}
```

Response:

```json
{
  "event_id": "evt_internal_123",
  "status": "accepted"
}
```

---

# Get Customer

```http
GET /v1/customers/{customer_id}
```

---

# Get Memories

```http
GET /v1/customers/{customer_id}/memories
```

Optional parameters:

```text
type
status
limit
cursor
```

---

# Get Timeline

```http
GET /v1/customers/{customer_id}/timeline
```

---

# Query Memory

```http
POST /v1/memory/query
```

Request:

```json
{
  "customer_id": "cus_123",
  "query": "What problems has this customer experienced recently?"
}
```

Response:

```json
{
  "answer": "The customer has recently experienced Shopify integration problems.",
  "memories": [
    {
      "id": "mem_123",
      "content": "Customer is having difficulty connecting Shopify.",
      "confidence": 0.94
    }
  ],
  "sources": [
    {
      "event_id": "evt_123"
    }
  ]
}
```

---

# AI Context API

This is one of the most important APIs.

```http
POST /v1/memory/context
```

Request:

```json
{
  "customer_id": "cus_123",
  "task": "respond_to_support_ticket",
  "query": "Customer says Shopify is broken again."
}
```

Response:

```json
{
  "customer_context": {
    "important_memories": [],
    "active_problems": [],
    "preferences": [],
    "recent_events": [],
    "relationships": []
  }
}
```

This endpoint is designed for AI agents.

---

# 🧪 Playground

The dashboard should contain an AI Memory Playground.

Example:

```text
Customer:
cus_123

Query:

┌─────────────────────────────────────────────┐
│ Why did this customer downgrade?            │
└─────────────────────────────────────────────┘

                 [ Ask Memory ]

Answer
───────────────────────────────────────────────

The customer downgraded after multiple Shopify
integration issues and two support interactions.

Evidence

• Shopify integration failure
• Support conversation
• Second support conversation
• Subscription downgrade
```

The UI should allow developers to inspect exactly why a memory was retrieved.

---

# 📊 Dashboard

The dashboard should contain:

## Overview

```text
Total Customers
Total Events
Total Memories
Events Processed
AI Queries
API Usage
```

## Customer Explorer

```text
Search customer
        ↓
Customer profile
        ↓
Timeline
        ↓
Memories
        ↓
Relationships
        ↓
AI Query
```

## Memory Explorer

Show:

```text
Memory
Type
Importance
Confidence
Created
Last Seen
Status
Source
```

## Event Explorer

Show raw events and processing status.

---

# 🕸️ Memory Graph

Use React Flow to visualize relationships.

Example:

```text
                ┌──────────────┐
                │    John      │
                └──────┬───────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
          ▼            ▼            ▼
       Shopify      Support       Pro Plan
          │            │
          ▼            ▼
      Integration    Ticket
       Problem
```

Clicking a node should show:

* Entity information
* Related memories
* Events
* Confidence
* Timeline

---

# 🔐 Security

Security is a core requirement.

Implement:

### API key authentication

Never store raw API keys.

Store:

```text
hashed_api_key
```

### Tenant isolation

Every query must be scoped to:

```text
organization_id
project_id
```

Never allow cross-tenant data access.

### Encryption

Sensitive data must be encrypted in transit and protected at rest.

### PII controls

Support:

```text
PII detection
PII redaction
data deletion
customer deletion
memory deletion
event deletion
```

### Audit logs

Track:

```text
API access
Memory changes
Authentication
Configuration changes
Data deletion
```

---

# 🧹 Data Retention

Projects should be able to configure:

```text
Event retention
Memory retention
Conversation retention
Embedding retention
```

Example:

```text
Events:
90 days

Memories:
indefinite

Conversations:
30 days
```

These should be configurable.

---

# 🔌 Integrations

Do not build all integrations in V1.

Start with:

```text
Generic Events API
```

Then add:

```text
Stripe
Intercom
Zendesk
HubSpot
PostHog
Slack
Salesforce
```

Each integration should normalize external events into the internal event schema.

Example:

```text
Stripe subscription.updated
            ↓
Normalized Event
            ↓
Memory Engine
```

---

# 📦 TypeScript SDK

Package:

```bash
npm install @ai-memory/sdk
```

Usage:

```typescript
import { MemoryClient } from "@ai-memory/sdk";

const memory = new MemoryClient({
  apiKey: process.env.MEMORY_API_KEY
});

await memory.events.track({
  customerId: "cus_123",
  type: "feature_used",
  data: {
    feature: "campaign_builder"
  }
});
```

Query:

```typescript
const result = await memory.query({
  customerId: "cus_123",
  query: "What problems has this customer experienced?"
});
```

Context:

```typescript
const context = await memory.context({
  customerId: "cus_123",
  task: "support_response"
});
```

---

# 🐍 Python SDK

Future package:

```bash
pip install ai-memory
```

Usage:

```python
from ai_memory import MemoryClient

memory = MemoryClient(
    api_key="..."
)

memory.events.track(
    customer_id="cus_123",
    type="feature_used",
    data={
        "feature": "campaign_builder"
    }
)
```

---

# 🧪 Testing

The project must have automated tests.

## Unit tests

Test:

```text
Memory extraction
Memory consolidation
Memory ranking
Memory decay
Conflict resolution
Entity extraction
Query parsing
```

## Integration tests

Test:

```text
API → Database
API → Queue
Worker → Database
Worker → Memory engine
Vector retrieval
```

## End-to-end tests

Example:

```text
Create project
 ↓
Create customer
 ↓
Send event
 ↓
Process event
 ↓
Generate memory
 ↓
Query memory
 ↓
Verify answer
```

---

# 📈 Observability

Track:

```text
API latency
Event processing latency
Queue depth
Extraction latency
Consolidation decisions
Embedding latency
Database latency
Retrieval latency
Error rate
Memory creation rate
Memory update rate
```

Every answer should be traceable to:

```text
query
 ↓
question analysis
 ↓
retrieved memories
 ↓
source events
 ↓
answer strategy
 ↓
response
```

---

# 💰 Cost Optimization

Understanding costs CPU, not tokens — but most events still carry no durable meaning and
should never reach extraction at all.

Pipeline:

```text
10,000 events
      ↓
Cheap rule-based filtering
      ↓
Potentially meaningful events
      ↓
Rule-based extraction
      ↓
Memory consolidation
```

Examples of low-value events:

```text
page_view
mouse_move
heartbeat
health_check
```

Examples of potentially high-value events:

```text
support_message
subscription_changed
payment_failed
feedback
cancellation
feature_used
goal_created
purchase
```

Make event importance configurable per project.

---

# 🧠 Deterministic Language Engine

The engine depends on an embedder interface, not on any provider.

```python
class Embedder(Protocol):

    model: str
    dimensions: int

    async def embed_one(self, text: str) -> list[float]:
        ...
```

Implemented by:

```text
LocalEmbedder   # hashed lexical vectors, in-process, no model files
```

Everything else — classification, extraction, consolidation, question analysis and answer
composition — is pure Python over lexicons and templates. There is no provider to
configure, no key to rotate and no per-request cost.

---

# 🛠️ Environment Variables

Create:

```text
.env.example
```

Example:

```env
# Application
APP_ENV=development
APP_URL=http://localhost:3000
API_URL=http://localhost:8000

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/memory

# Redis
REDIS_URL=redis://localhost:6379

# Language engine (deterministic, in-process)
EMBEDDING_DIMENSIONS=1536
NLP_MAX_MEMORIES_PER_EVENT=6

# Authentication
JWT_SECRET=
API_KEY_SECRET=

# CORS
CORS_ORIGINS=http://localhost:3000
```

---

# 🧑‍💻 Local Development

Required services:

```text
PostgreSQL
Redis
```

Install Python dependencies:

```bash
uv sync
```

Run migrations:

```bash
alembic upgrade head
```

Start API:

```bash
uv run uvicorn apps.api.app.main:app --reload --port 8000
```

Start worker:

```bash
uv run python apps/worker/main.py
```

Start frontend:

```bash
pnpm install
pnpm dev
```

Frontend:

```text
http://localhost:3000
```

API:

```text
http://localhost:8000
```

API documentation:

```text
http://localhost:8000/docs
```

---

# 🗺️ Development Roadmap

## Phase 1 — Foundation

Build:

```text
Project authentication
API keys
Organizations
Customers
Events
PostgreSQL
FastAPI
```

Goal:

```text
POST /events
```

works reliably.

---

# Phase 2 — Memory Engine

Build:

```text
Event normalization
Memory extraction
Entity extraction
Memory storage
Importance scoring
Confidence scoring
```

Goal:

```text
Event
 ↓
Memory
```

---

# Phase 3 — Vector Retrieval

Add:

```text
pgvector
Embeddings
Semantic search
Hybrid retrieval
Memory ranking
```

Goal:

```text
Question
 ↓
Relevant memories
```

---

# Phase 4 — Memory Consolidation

Build:

```text
Duplicate detection
Memory merging
Conflict resolution
Memory versions
Temporal memory
Memory decay
```

Goal:

```text
Multiple events
 ↓
One evolving memory
```

---

# Phase 5 — AI Query Engine

Build:

```text
Query understanding
Retrieval
Context building
Answer composition
Evidence attribution
```

Goal:

```text
"What happened with this customer?"
```

returns an evidence-backed answer.

---

# Phase 6 — Dashboard

Build:

```text
Customer Explorer
Timeline
Memory Explorer
Memory Graph
AI Playground
Usage Dashboard
API Key Management
```

---

# Phase 7 — SDK

Release:

```text
@ai-memory/sdk
```

Provide:

```text
events.track()
memory.query()
memory.context()
customers.get()
```

---

# Phase 8 — Integrations

Add:

```text
Stripe
Intercom
Zendesk
HubSpot
PostHog
Slack
```

---

# Phase 9 — AI Agent Context

Build:

```http
POST /v1/memory/context
```

Allow external AI agents to retrieve customer context before generating responses.

---

# Phase 10 — Advanced Memory

Built:

```text
Long-term memory
Memory graph
Causal timeline candidates
Goal tracking                 → goals open, progress and close from later evidence
Customer intent
Predictive signals            → trajectory, churn risk and expansion, with the evidence
Memory summarization
Cross-session agent memory    → an agent is briefed from memory and writes back on close
Personalized recommendations  → ranked next best actions, each citing its memories
Automatic memory cleanup
Memory confidence calibration
```

All of it deterministic: a forecast is a measurement of stored rows, so it can be replayed,
argued with, and traced back to the memories that produced it.

---

# 🧪 Example End-to-End Flow

Developer sends:

```json
{
  "customer_id": "cus_123",
  "event_type": "support_message",
  "data": {
    "message": "I've tried connecting Shopify three times but it still doesn't work."
  }
}
```

System:

```text
API
 ↓
Validate
 ↓
Store Event
 ↓
Queue
 ↓
Worker
 ↓
Rule-based extraction
 ↓
Entity: Shopify
 ↓
Memory: Shopify integration problem
 ↓
Search existing memories
 ↓
Merge if necessary
 ↓
Generate embedding
 ↓
Store
```

Later:

```http
POST /v1/memory/query
```

```json
{
  "customer_id": "cus_123",
  "query": "What integration problems does this customer have?"
}
```

System:

```text
Query
 ↓
Embedding
 ↓
Vector search
 ↓
Structured filters
 ↓
Memory ranking
 ↓
Context builder
 ↓
Answer composer
```

Response:

```json
{
  "answer": "The customer has repeatedly experienced problems connecting Shopify.",
  "evidence": [
    {
      "memory_id": "mem_123",
      "confidence": 0.94
    }
  ]
}
```

---

# 🎯 V1 Success Criteria

The MVP is complete when a developer can:

1. Create an account.
2. Create a project.
3. Generate an API key.
4. Create a customer.
5. Send customer events.
6. See events in the dashboard.
7. Have AI automatically extract memories.
8. See memories associated with a customer.
9. Search memories semantically.
10. Ask natural-language questions about a customer.
11. Receive an answer with evidence.
12. View the customer's timeline.
13. View basic entity relationships.
14. Delete customer data.
15. Integrate the TypeScript SDK.

---

# 🚫 Do Not Over-Engineer V1

Do NOT initially build:

```text
❌ Kafka
❌ Kubernetes
❌ Neo4j
❌ Elasticsearch
❌ Multiple microservices
❌ An LLM in the hot path
❌ A trained embedding model
❌ 20 integrations
❌ Complex agent framework
```

Start with:

```text
FastAPI
PostgreSQL
pgvector
Redis
Python Worker
Deterministic language engine
Next.js
```

The complexity should live in the **Memory Engine**, not in unnecessary infrastructure.

---

# 🧠 Core Product Moat

The long-term product advantage should not be:

> "We store vectors."

It should be:

> **"We understand how a customer's memory evolves over time."**

The Memory Engine should eventually understand:

```text
Events
   ↓
Facts
   ↓
Preferences
   ↓
Problems
   ↓
Goals
   ↓
Relationships
   ↓
Temporal patterns
   ↓
Customer context
```

The platform should continuously transform:

```text
Raw customer activity
```

into:

```text
A living customer memory.
```

---

# 🌎 Long-Term Vision

Eventually any SaaS should be able to install:

```bash
npm install @ai-memory/sdk
```

and add:

```typescript
memory.events.track(...)
```

Then every AI agent in their product can access:

```text
┌───────────────────────────────────────┐
│          CUSTOMER MEMORY              │
├───────────────────────────────────────┤
│ Facts                                 │
│ Preferences                           │
│ Problems                              │
│ Goals                                 │
│ Behaviors                             │
│ Relationships                         │
│ Conversations                        │
│ Product usage                         │
│ Subscription history                  │
│ Important events                      │
└───────────────────────────────────────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
    Support      Sales     Product
      AI          AI         AI
```

### Final positioning

> **AI Memory Layer — Persistent memory infrastructure for AI-powered SaaS.**

Or:

> **Give your AI agents a memory of every customer.**

---

# ⭐ Product Principle

> **Store events. Extract memories. Understand relationships. Retrieve context. Let AI reason over it.**

The system should always preserve the distinction between:

```text
WHAT HAPPENED
     ↓
WHAT WE KNOW
     ↓
WHAT WE INFER
```

Never present an inference as a fact.
Never hide the evidence behind a memory.
Never sacrifice tenant isolation for convenience.

This principle should guide the entire architecture.
