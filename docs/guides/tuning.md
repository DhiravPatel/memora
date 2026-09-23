# Tuning a project

Every knob below lives in `projects.settings`. The dashboard's **Settings → Engine** tab
renders one labelled control per knob — sliders for the ratios, number fields with units
for the windows, a key/value editor for the per-event-type table — and the form is
generated from the same schema the API validates against, so the two can never drift.

Three endpoints cover it:

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/projects/{id}/settings` | Current values, defaults, and the field schema (label, group, bounds, step, unit, help text) |
| `PUT /v1/projects/{id}/settings` | Replace the settings document (admin only, audited as `configuration_change`) |
| `PATCH /v1/projects/{id}` | Rename and/or patch settings in one call |

Writes are validated before they reach the database: a value outside its bounds, of the
wrong type, or that contradicts another setting is refused with `422` and the project is
left exactly as it was. Keys this version does not recognise are stored untouched, so a
newer engine's settings survive a round trip through an older dashboard.

```json
{
  "min_event_importance": 0.2,
  "event_importance": { "page_view": 0.02, "trial_extended": 0.9 },
  "consolidation_similarity": 0.45,
  "decay_days": 90,
  "ranking_weights": {
    "similarity": 0.35,
    "importance": 0.20,
    "confidence": 0.20,
    "recency": 0.15,
    "relationship": 0.10
  },
  "context_token_budget": 2000,
  "retention": { "events_days": 90, "memories_days": 0, "conversations_days": 30 },
  "pii_redaction_enabled": true,
  "rate_limit_per_minute": 600,
  "monthly_event_quota": 0,
  "integrations": { "stripe": { "signing_secret": "whsec_…" } }
}
```

## `min_event_importance` — the cost dial

Events scoring below this are never extracted. Raise it to keep the memory sparse and the
pipeline cheap, lower it to remember more. The Event Explorer shows each event's score and whether it was `skipped`, so you can
tune against real traffic rather than guesses.

## `event_importance` — your domain, not ours

The built-in table covers common SaaS events. Override anything that means more (or less)
in your product. A payload field `{"importance": 0.9}` overrides both for a single event.

## `consolidation_similarity` — duplicates vs. conflation

Lower merges more aggressively (fewer, broader memories); higher keeps memories separate.
The default (0.45) is calibrated for the local lexical embedder, where restatements of the
same issue land around 0.45-0.65 and unrelated statements below 0.15. Below ~0.3, distinct
problems start being merged.

## `decay_days`

The base window for transient memory types. Durable types ignore it entirely — a fact does
not stop being true because it is old.

## `ranking_weights`

If answers feel stale, raise `recency`. If they feel scattershot, raise `importance` and
`confidence`. The weights are normalised before ranking, so only their ratio matters — the
dashboard shows each one's resulting share as a percentage. They cannot all be zero.
Use the playground's retrieval trace to see the effect on real questions.

## `context_token_budget`

The default ceiling for `/v1/memory/context`. Callers may ask for less per request; a
response that hits the ceiling is marked `truncated` rather than overflowing it.

## `rate_limit_per_minute` and `monthly_event_quota`

The rate limit is a per-project fixed window enforced in Redis; responses always carry
`X-RateLimit-*` headers. The quota is read from the project's own usage counters, so it
agrees with the number the dashboard shows. `0` disables the quota. A non-zero quota lower
than one minute of the rate limit is refused — it would make the rate limit meaningless. Both fail **open** if
Redis is unavailable — losing an event is permanent, letting a few extra through is not.

## `integrations.<provider>.signing_secret`

Required before a provider's webhook is accepted. Set `allow_unsigned: true` only for
internal systems on a private network.

## `health_weights`

How much each factor moves the health score, from a baseline of 70. Negative pulls down.
The defaults are a starting point, not a claim about your product — if churn language
matters more to you than an open problem does, say so here. Values are clamped to ±40 so
no single factor can swamp the scale, and an unknown factor name is refused rather than
silently ignored.

The portfolio view and the single-customer view read the same weights, so they cannot
disagree.

## `retention`

`0` means keep indefinitely. The worker applies retention nightly and writes an audit entry.
Finished agent conversations age out on the conversation window; the summary memory each
one produced is kept under the memory window.

## Learned vocabulary

Not a setting — a project's own vocabulary is *mined* from its memories nightly, and used
to widen retrieval. Settings → Engine shows what has been learned, with the number of
memories behind each pair, and a **Rebuild** button for when a lot of new memory has
arrived at once. The way to change it is to change what your customers write.
