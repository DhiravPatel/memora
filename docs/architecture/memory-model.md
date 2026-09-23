# Memory model

## Four kinds of thing

| Concept | Table | Mutable? | Meaning |
| --- | --- | --- | --- |
| Event | `events` | no | Something that happened |
| Memory | `memories` | yes, with versions | Something we believe about the customer |
| Entity | `entities` | yes | Something the customer interacts with |
| Relationship | `relationships` | yes | How entities connect |

`WHAT HAPPENED → WHAT WE KNOW → WHAT WE INFER` is preserved end to end: a memory carries
`source_event_ids`, an answer carries the memory ids it used, and the API returns both.

## Memory lifecycle

```
extraction → candidate memory
      │
      ├── identical content hash                 → evidence added
      ├── ≥ auto-merge similarity (0.82)          → merged, newest wording kept
      ├── ≥ project threshold (default 0.45)      → rules decide: merge / update / conflict
      ├── different memory type                   → new memory
      └── below threshold                         → new memory
```

The decision is a pure function — `memory_engine.consolidation.rules.decide` — so every
branch is unit-tested and every decision is reproducible from the signals stored on the
memory version: similarity, lexical overlap, shared entities, novelty and the rule that
fired.

### Conflicts

When the rules detect a contradiction — a changed contact channel, a different current
plan, a resolved problem, or opposite statements about the same subject — the engine scores
both sides rather than assuming the newer one wins. `memory_engine.consolidation.conflict` scores both sides on recency (0.35),
confidence (0.25), frequency (0.15), explicitness (0.15) and source trust (0.10). Ties go
to the incumbent — churn in memory is expensive. The replaced memory is superseded, never
removed, so "customer preferred email until August" remains answerable.

## Temporal behaviour

Durable types (`fact`, `preference`, `relationship`, `subscription`, `feedback`) never
expire. Transient types decay on a per-type multiplier of the project's decay window, and
retrieval weights freshness by a type-specific half-life: an `intent` halves in 7 days, a
`fact` in 180.

## Importance

Extraction proposes an importance from the statement's type, sentiment, urgency and churn
language; the engine then recomputes it with signals the sentence cannot carry — the
originating event's importance, the business relevance of the memory type, how often the
memory has been confirmed, and how explicit the language is. Weights are in
`memory_engine/ranking/importance.py` and the retrieval weights are configurable per
project.
