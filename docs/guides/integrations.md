# Adding an integration

V1 ships the generic Events API only. Everything else — Stripe, Intercom, Zendesk,
HubSpot, PostHog, Slack, Salesforce — is a normalizer that maps an external payload onto
the internal event schema and calls `POST /v1/events`.

An integration must decide four things:

1. **Customer identity.** Which external field is the stable customer id?
2. **Event type.** Use a name from the importance table where one fits
   (`subscription_changed`, `support_message`, `payment_failed`), so default scoring applies.
3. **Idempotency.** Pass the provider's own event id as `external_event_id`; webhooks are
   redelivered and the API must store the event exactly once.
4. **Text.** Put human-written text in `message`, `body`, `feedback` or `reason` — those
   fields raise the importance score and carry most of the meaning for extraction. When the
   text lives elsewhere (`details.reason`, `ticket.description`), say so in the type's
   memory contract's `text_field` rather than renaming it.

Then write the type's **memory contract** — required fields, their types, the text field, the
importance — and run your fixtures through `POST /v1/contracts/{event_type}/test` in CI. Start
in `warn` mode: events that break it are kept and the Contracts page shows exactly how
(`amount` expected a number, received `string ("₹500")`). Switch to `enforce` once the report
is clean; a payload that breaks it is then refused with `422 contract_violation` instead of
being remembered wrongly. `POST /v1/contracts/{event_type}/draft` writes a first version from
the traffic you already send. See [Memory contracts](../api/README.md#memory-contracts).

```
Stripe customer.subscription.updated
        ↓ normalize
{ "customer_id": "cus_123",
  "event_type": "subscription_changed",
  "external_event_id": "evt_1PabcStripe",
  "occurred_at": "2026-09-17T10:00:00Z",
  "data": { "plan": "Pro", "previous_plan": "Starter", "mrr_delta": 40 } }
        ↓ POST /v1/events
Memory engine
```

Webhook handlers should verify the provider's signature, normalize, and return quickly —
the memory pipeline is already asynchronous, so there is nothing to wait for.
