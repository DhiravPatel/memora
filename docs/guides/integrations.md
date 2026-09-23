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
   fields raise the importance score and carry most of the meaning for extraction.

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
