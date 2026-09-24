"""The actions an agent takes, by family — shared by the guardrails, which judge them, and
the fact document, which remembers them (§26 3.2, 4.5).

Any action name is accepted anywhere; these are the ones Memora knows the meaning of.
"""

from __future__ import annotations

import re

SELLING = frozenset({"offer_upgrade", "offer_expansion", "upsell", "cross_sell", "offer_add_on"})
PROMOTION = frozenset({"send_marketing", "send_promotion", "request_review", "request_referral"})
CONTACT = frozenset({"contact_customer", "send_message", "send_email", "call_customer", *PROMOTION})
CLOSING = frozenset({"close_ticket", "mark_resolved", "resolve_ticket"})
MONEY = frozenset({"offer_discount", "issue_credit", "process_refund", "waive_fee"})
ACCOUNT = frozenset({"cancel_subscription", "downgrade_plan", "change_plan", "delete_account", "pause_subscription"})

ACTION_CATALOG: dict[str, str] = {
    **dict.fromkeys(sorted(SELLING), "selling"),
    **dict.fromkeys(sorted(PROMOTION), "promotion"),
    **dict.fromkeys(sorted(CONTACT - PROMOTION), "contact"),
    **dict.fromkeys(sorted(CLOSING), "closing"),
    **dict.fromkeys(sorted(MONEY), "money"),
    **dict.fromkeys(sorted(ACCOUNT), "account"),
    "escalate": "support",
    "create_ticket": "support",
    "schedule_call": "contact",
}
FAMILIES = ("selling", "promotion", "contact", "closing", "money", "account", "support")

# The channel an action uses when the request does not name one.
ACTION_CHANNELS = {"call_customer": "phone", "schedule_call": "phone", "send_email": "email"}

ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


def normalise_action(action: str) -> str:
    return action.strip().lower().replace("-", "_").replace(" ", "_")


def families_of(action: str) -> set[str]:
    """Every family an action belongs to. Promotion is also contact: a marketing email is
    outreach, and "no more than three contacts a week" should count it."""
    found = {ACTION_CATALOG[action]} if action in ACTION_CATALOG else set()
    if action in CONTACT:
        found.add("contact")
    return found
