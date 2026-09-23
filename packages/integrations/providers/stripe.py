"""Stripe → internal events.

Billing is the single highest-signal source a SaaS has: a failed payment, a downgrade or
a refund says more about a customer than a month of product telemetry.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from integrations.base import NormalizedEvent, first_present, timestamp_from
from integrations.signature import verify_timestamped

# Stripe event type → internal event type.
EVENT_MAP: dict[str, str] = {
    "customer.subscription.created": "subscription_changed",
    "customer.subscription.updated": "subscription_changed",
    "customer.subscription.deleted": "subscription_cancelled",
    "customer.subscription.trial_will_end": "trial_ending",
    "invoice.payment_failed": "payment_failed",
    "invoice.payment_succeeded": "invoice_paid",
    "invoice.finalized": "invoice_issued",
    "charge.refunded": "refund_issued",
    "charge.dispute.created": "payment_disputed",
    "checkout.session.completed": "purchase",
    "customer.created": "profile_updated",
    "customer.updated": "profile_updated",
}


class StripeIntegration:
    name = "stripe"
    signature_header = "Stripe-Signature"

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_timestamped(
            secret=secret, raw_body=raw_body, header=headers.get(self.signature_header.lower(), "")
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        stripe_type = str(payload.get("type", ""))
        event_type = EVENT_MAP.get(stripe_type)
        if event_type is None:
            return []

        obj = first_present(payload, "data.object", default={}) or {}
        previous = first_present(payload, "data.previous_attributes", default={}) or {}
        customer_id = first_present(obj, "customer", "id")
        if not customer_id:
            return []

        data: dict[str, Any] = {"stripe_event": stripe_type}
        plan = first_present(obj, "plan.nickname", "items.data.0.price.nickname", "items.data.0.plan.nickname")
        if plan:
            data["plan"] = plan
        previous_plan = first_present(previous, "plan.nickname", "items.data.0.price.nickname")
        if previous_plan:
            data["previous_plan"] = previous_plan

        amount = first_present(obj, "amount_due", "amount", "amount_total", "amount_refunded")
        if isinstance(amount, (int, float)):
            data["amount"] = round(amount / 100, 2)  # Stripe amounts are in minor units
            data["currency"] = str(obj.get("currency", "")).upper()

        failure = first_present(obj, "last_payment_error.message", "failure_message", "reason")
        if failure:
            data["reason"] = failure

        status = obj.get("status")
        if status:
            data["status"] = status
        if obj.get("cancel_at_period_end"):
            data["cancel_at_period_end"] = True

        return [
            NormalizedEvent(
                customer_id=str(customer_id),
                event_type=event_type,
                data=data,
                external_event_id=str(payload.get("id") or ""),
                occurred_at=timestamp_from(payload.get("created")),
                customer_email=first_present(obj, "customer_email", "email"),
                customer_name=first_present(obj, "customer_name", "name"),
                source="stripe",
            )
        ]
