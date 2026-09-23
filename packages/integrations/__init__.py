"""Integrations.

Every connector normalises an external payload into the internal event schema and proves
the payload is authentic. The memory engine never learns which system an event came from
beyond its ``source`` field, so adding a provider can never change how memory behaves.

Registering a new provider is one class and one registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

from common.errors import AuthenticationError, NotFoundError, ValidationError
from common.logging import get_logger
from integrations.base import Integration, NormalizedEvent
from integrations.providers.analytics import (
    GenericIntegration,
    HubSpotIntegration,
    PostHogIntegration,
)
from integrations.providers.stripe import StripeIntegration
from integrations.providers.support import (
    IntercomIntegration,
    SlackIntegration,
    ZendeskIntegration,
)

logger = get_logger(__name__)

REGISTRY: dict[str, Integration] = {
    integration.name: integration
    for integration in (
        StripeIntegration(),
        IntercomIntegration(),
        ZendeskIntegration(),
        SlackIntegration(),
        PostHogIntegration(),
        HubSpotIntegration(),
        GenericIntegration(),
    )
}

PROVIDERS: tuple[str, ...] = tuple(sorted(REGISTRY))


def get(provider: str) -> Integration:
    integration = REGISTRY.get(provider.strip().lower())
    if integration is None:
        raise NotFoundError(
            f"Unknown integration '{provider}'. Available: {', '.join(PROVIDERS)}."
        )
    return integration


def process(
    *,
    provider: str,
    payload: dict,
    raw_body: bytes,
    headers: dict[str, str],
    secret: str | None,
    require_signature: bool = True,
) -> list[NormalizedEvent]:
    """Verify and normalise a webhook payload."""
    integration = get(provider)

    if require_signature:
        if not secret:
            raise ValidationError(
                f"No signing secret configured for '{provider}'. Set "
                f"settings.integrations.{provider}.signing_secret on the project."
            )
        lowered = {key.lower(): value for key, value in headers.items()}
        if not integration.verify(secret=secret, raw_body=raw_body, headers=lowered):
            logger.warning("integration.signature_rejected", provider=provider)
            raise AuthenticationError(f"Invalid {provider} webhook signature.")

    events = list(integration.normalize(payload))
    logger.info("integration.normalized", provider=provider, events=len(events))
    return events


def describe() -> list[dict[str, str | None]]:
    """What the dashboard shows on the integrations screen."""
    return [
        {
            "provider": name,
            "signature_header": getattr(integration, "signature_header", None),
        }
        for name, integration in sorted(REGISTRY.items())
    ]


__all__ = [
    "PROVIDERS",
    "REGISTRY",
    "Integration",
    "NormalizedEvent",
    "describe",
    "get",
    "process",
]


def normalize_all(provider: str, payloads: Sequence[dict]) -> list[NormalizedEvent]:
    """Normalise several payloads (used by backfill scripts)."""
    integration = get(provider)
    return [event for payload in payloads for event in integration.normalize(payload)]
