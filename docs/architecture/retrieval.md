# Retrieval and ranking

## Five strategies, one result set

| Strategy | Answers | Implementation |
| --- | --- | --- |
| Semantic | "things like this" | pgvector cosine distance over memory embeddings |
| Keyword | exact terms, names, error codes | PostgreSQL `websearch_to_tsquery` + GIN index |
| Temporal | "recently", "last two weeks" | `last_seen_at` window from query analysis |
| Type | "problems", "billing" | top memories of the inferred types, to widen recall |
| Entity | "about Shopify" | memory ↔ entity link table |
| Relationship | one hop from the query's entities | `relationships` edges |

A vector index has no notion of *recent*, *only problems*, or *about Shopify*, so relying
on similarity alone produces confident, irrelevant context. Query analysis is rule-based
and costs nothing: it extracts the time window, the memory types being asked about, and
entity names before any search runs.

Inferred types **widen** recall rather than filtering it. "Why did this customer
downgrade?" looks like a subscription question, but the answer usually lives in the
problem memories — so the inferred type adds candidates and ranking decides. Only an
explicit `types` argument from the caller filters.

The strategies share one database session, so they execute in sequence rather than
concurrently — an `AsyncSession` does not permit concurrent operations.

## Ranking

```
score = ( w_sim·similarity + w_imp·importance + w_conf·confidence
        + w_rec·recency + w_rel·relationship_relevance ) / Σw
```

Defaults: 0.35 / 0.20 / 0.20 / 0.15 / 0.10, overridable globally via environment variables
and per project via `settings.ranking_weights`. Results are then diversified so one noisy
problem cannot fill the entire context window.

Every ranked result carries `explain()`: the per-signal breakdown and which strategies
retrieved it. That is what the dashboard playground renders, and what `include_trace`
returns from the API.

## Context building

The context builder groups memories into sections (active problems, important facts,
preferences, goals, feedback, product usage), drops near-duplicates by token overlap, and
stops adding items once the token budget is spent — marking the result `truncated` rather
than silently overflowing an agent's prompt.
