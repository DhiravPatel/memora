"""Concepts: what a sentence is *about*, independent of the words it used.

"The integration is completely broken" and "the connector has stopped functioning" share no
content word, and every lexical strategy — keyword, the hashed lexical embedding, even the
mined vocabulary — sees two unrelated sentences. They are the same complaint. This module
closes that gap without a model: a shipped lexicon of *paraphrase families*, each a concept
with the many ways people say it, and a matcher that turns text into the set of concepts it
mentions. Both sentences above become ``{integration, broken}``.

Properties that matter:

* **Deterministic and inspectable.** A match can say *which phrase* produced *which
  concept*, so retrieval can explain "matched on `broken` via 'stopped functioning'".
* **Robust to inflection and derivation.** Phrases and text are compared on
  :func:`nlp.tokenize.root`, so "failing", "failed" and "failure" all reach ``broken``.
* **Recall only.** Concepts widen which memories are *considered*; they never decide what is
  true, and they are ranked below an exact lexical match of the same strength.

Negation is deliberately not handled: "the sync is not broken" still mentions the broken
concept, and for *finding* the memories a question is about, that is the right call.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from nlp.tokenize import expand_contractions, root, tokenize

# concept id -> (label, phrases). Phrases are matched on word roots, longest first.
CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "broken": (
        "Something is not working",
        (
            "broken", "not working", "does not work", "do not work", "did not work", "stopped working",
            "stop working", "not functioning", "stopped functioning", "not functional", "failing", "fails",
            "failed", "failure", "error", "errors", "crash", "crashed", "crashing", "dead", "hosed", "borked",
            "busted", "kaput", "glitch", "glitchy", "bug", "buggy", "malfunction", "malfunctioning",
            "outage", "outages", "unavailable", "unresponsive", "down", "went down", "is down", "defect",
            "not loading", "will not load", "wont load", "keeps breaking", "stopped", "stops",
            "dies", "died", "dying", "keeps dying", "blew up", "falls over", "fell over", "choked",
        ),
    ),
    "slow": (
        "Something is slow",
        (
            "slow", "slowly", "sluggish", "laggy", "lag", "lagging", "takes forever", "take forever",
            "taking forever", "timeout", "timeouts", "time out", "times out", "timed out", "latency",
            "hangs", "hanging", "freezes", "freezing", "frozen", "spinning", "performance", "speed",
            "loading forever", "long time", "delay", "delays", "delayed",
        ),
    ),
    "integration": (
        "An integration or data connection",
        (
            "integration", "integrations", "integrate", "connector", "connectors", "connection",
            "connections", "connect", "sync", "syncing", "synced", "synchronisation", "synchronization",
            "plugin", "plugins", "add on", "addon", "extension", "api", "webhook", "webhooks", "pipeline",
            "import", "importer", "importing", "loader", "data loader", "feed", "feeds", "etl", "zapier",
            "shopify", "stripe", "salesforce", "hubspot", "slack", "quickbooks", "xero", "woocommerce",
        ),
    ),
    "data_export": (
        "Getting data out",
        ("export", "exports", "exporting", "download", "downloads", "csv", "excel", "spreadsheet", "xlsx", "backup", "extract"),
    ),
    "data_loss": (
        "Data went missing",
        (
            "data loss", "lost data", "lost", "missing data", "missing", "deleted", "disappeared",
            "vanished", "gone", "wiped", "overwritten", "corrupted", "corruption", "duplicate", "duplicates",
            "duplicated",
        ),
    ),
    "billing": (
        "Money: invoices, charges, payments",
        (
            "billing", "bill", "bills", "billed", "invoice", "invoices", "invoiced", "charge", "charges",
            "charged", "overcharged", "double charged", "payment", "payments", "paid", "pay", "refund",
            "refunds", "refunded", "credit card", "card declined", "declined", "receipt", "pricing", "price",
            "prices", "cost", "costs", "expensive", "fee", "fees", "tax", "vat", "gst", "discount", "coupon",
        ),
    ),
    "cancellation": (
        "Leaving",
        (
            "cancel", "cancellation", "cancelled", "churn", "churned", "leave", "leaving", "quit",
            "terminate", "termination", "stop using", "close our account", "close the account",
            "switch to", "switching to", "move to", "competitor", "competitors", "alternative",
            "alternatives", "not renew", "not renewing",
        ),
    ),
    "upgrade": (
        "Wanting more",
        (
            "upgrade", "upgraded", "upgrading", "more seats", "add seats", "additional seats", "add users",
            "more users", "expand", "expansion", "enterprise", "higher tier", "higher plan", "roll out",
            "rollout", "company wide", "whole company", "another team", "scale up",
        ),
    ),
    "downgrade": (
        "Wanting less",
        ("downgrade", "downgraded", "downgrading", "cheaper plan", "smaller plan", "fewer seats", "reduce seats", "remove seats", "lower tier", "cut back"),
    ),
    "login": (
        "Getting in: accounts and authentication",
        (
            "login", "log in", "logging in", "logged out", "sign in", "signing in", "signin", "sign on",
            "authentication", "authenticate", "auth", "password", "reset password", "locked out",
            "lockout", "access", "permission", "permissions", "sso", "saml", "single sign on", "2fa", "mfa",
            "two factor", "otp", "verification code",
        ),
    ),
    "onboarding": (
        "Getting started",
        (
            "onboarding", "onboard", "setup", "set up", "setting up", "getting started", "get started",
            "configuration", "configure", "configured", "install", "installation", "installed",
            "implementation", "migrate", "migration", "migrating", "go live", "launch", "kickoff",
        ),
    ),
    "reporting": (
        "Reports, dashboards and analytics",
        ("report", "reports", "reporting", "dashboard", "dashboards", "analytics", "chart", "charts", "graph", "metrics", "insights", "kpi", "statistics", "stats"),
    ),
    "notifications": (
        "Alerts and messages from the product",
        ("notification", "notifications", "notify", "alert", "alerts", "reminder", "reminders", "email notification", "push notification", "digest"),
    ),
    "support": (
        "Getting help",
        (
            "support", "help", "helpdesk", "ticket", "tickets", "agent", "response time", "no response",
            "no reply", "ignored", "escalate", "escalation", "escalated", "contact support", "call back",
        ),
    ),
    "usability": (
        "Hard to use or understand",
        (
            "confusing", "confused", "unclear", "not clear", "do not understand", "hard to use",
            "difficult", "complicated", "complex", "unintuitive", "not intuitive", "clunky", "cannot find",
            "can not find", "hard to find", "where is", "how do i", "learning curve",
        ),
    ),
    "praise": (
        "Happy with it",
        (
            "love", "loves", "great", "excellent", "amazing", "awesome", "fantastic", "happy", "satisfied",
            "delighted", "impressed", "perfect", "brilliant", "thank you", "thanks", "helpful", "recommend",
        ),
    ),
    "frustration": (
        "Unhappy with it",
        (
            "frustrated", "frustrating", "frustration", "annoyed", "annoying", "angry", "upset",
            "disappointed", "disappointing", "unhappy", "fed up", "terrible", "awful", "horrible",
            "unacceptable", "ridiculous", "waste", "useless", "worst",
        ),
    ),
    "mobile": (
        "The mobile app",
        ("mobile", "ios", "android", "iphone", "ipad", "tablet", "mobile app", "app store", "play store"),
    ),
    "security": (
        "Security and compliance",
        ("security", "secure", "breach", "vulnerability", "hacked", "compliance", "gdpr", "soc2", "soc 2", "hipaa", "audit", "encryption", "privacy"),
    ),
    "orders": (
        "Orders and checkout",
        ("order", "orders", "checkout", "check out", "cart", "basket", "purchase", "purchases", "transaction", "transactions"),
    ),
    "shipping": (
        "Delivery and fulfilment",
        ("shipping", "shipment", "shipments", "ship", "shipped", "delivery", "deliveries", "deliver", "delivered", "tracking", "courier", "fulfilment", "fulfillment", "dispatch", "warehouse"),
    ),
    "feature_request": (
        "Asking for something the product does not do",
        (
            "feature request", "would like", "wish", "wishlist", "please add", "can you add", "could you add",
            "request", "requested", "requesting", "missing feature", "need a way", "would be nice",
            "would be great", "suggestion", "suggest", "roadmap", "support for",
        ),
    ),
    "users": (
        "People and seats on the account",
        ("user", "users", "seat", "seats", "team", "teammate", "teammates", "member", "members", "invite", "invited", "admin", "role", "roles"),
    ),
    "contact": (
        "How to reach the customer",
        ("email", "e mail", "whatsapp", "phone", "call", "sms", "text message", "telegram", "contact", "reach"),
    ),
    "trial": (
        "Evaluating the product",
        ("trial", "free trial", "evaluate", "evaluating", "evaluation", "pilot", "proof of concept", "poc", "demo", "compare", "comparing", "considering"),
    ),
    "renewal": (
        "Renewing the contract",
        ("renew", "renewal", "renewing", "renewed", "contract", "annual", "yearly", "extend"),
    ),
}


@dataclass(slots=True, frozen=True)
class ConceptHit:
    concept: str
    phrase: str


def _roots(text: str) -> list[str]:
    return [root(token) for token in tokenize(expand_contractions(text))]


# (roots tuple) -> concepts, and the phrase as written, precompiled once.
_INDEX: dict[tuple[str, ...], list[tuple[str, str]]] = {}
for _concept, (_label, _phrases) in CONCEPTS.items():
    for _phrase in _phrases:
        _key = tuple(_roots(_phrase))
        if _key:
            _INDEX.setdefault(_key, []).append((_concept, _phrase))
_MAX_WIDTH = max((len(key) for key in _INDEX), default=1)


def concept_hits(text: str) -> list[ConceptHit]:
    """Every concept the text mentions, with the phrase that found it. Longest phrase first."""
    roots = _roots(text)
    hits: list[ConceptHit] = []
    seen: set[str] = set()
    position = 0
    while position < len(roots):
        matched = 0
        for width in range(min(_MAX_WIDTH, len(roots) - position), 0, -1):
            entries = _INDEX.get(tuple(roots[position : position + width]))
            if entries:
                for concept, phrase in entries:
                    if concept not in seen:
                        seen.add(concept)
                        hits.append(ConceptHit(concept=concept, phrase=phrase))
                matched = width
                break
        position += matched or 1
    return hits


def concepts_for(text: str) -> list[str]:
    """The sorted set of concept ids a text mentions — what memories store and index."""
    return sorted({hit.concept for hit in concept_hits(text)})


def concepts_for_many(texts: Iterable[str]) -> list[str]:
    found: set[str] = set()
    for text in texts:
        found.update(concepts_for(text))
    return sorted(found)


def dice(query: Iterable[str], memory: Iterable[str]) -> float:
    """How much two concept sets agree: 2|Q∩M| / (|Q|+|M|).

    Rewards a memory for being *about* what was asked and nothing much else — a memory that
    mentions every concept under the sun overlaps everything, and should not win for it.
    """
    q, m = set(query), set(memory)
    if not q or not m:
        return 0.0
    return 2 * len(q & m) / (len(q) + len(m))


def label(concept: str) -> str:
    return CONCEPTS.get(concept, (concept, ()))[0]
