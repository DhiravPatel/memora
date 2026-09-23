"""Project settings: one schema, shared by the engine, the API and the dashboard.

Settings were previously a free-form JSON blob: the dashboard shipped a textarea, nothing
validated the values, and a typo like ``min_event_importance: 5`` silently stopped every
event from ever being extracted. This module makes the tunables a described, bounded,
validated set — the form renders from the same definitions the writer enforces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from common.crypto import encrypt, is_encrypted
from common.enums import MemoryType
from common.errors import ValidationError
from common.settings import get_settings
from database.models import Project
from memory_engine.analytics.health import MAX_WEIGHT, MIN_WEIGHT
from memory_engine.analytics.health import WEIGHTS as HEALTH_WEIGHTS
from memory_engine.policy import PolicyError, compile_policy

FieldKind = Literal["number", "percent", "boolean", "map", "weights", "text", "policies"]


@dataclass(slots=True, frozen=True)
class SettingField:
    key: str
    label: str
    group: str
    kind: FieldKind
    default: Any
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str | None = None
    # Keys a map/weights field is expected to contain (free-form maps leave this empty).
    keys: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "kind": self.kind,
            "default": self.default,
            "help": self.help,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "unit": self.unit,
            "keys": list(self.keys),
        }


GROUPS: tuple[tuple[str, str], ...] = (
    ("extraction", "Extraction"),
    ("consolidation", "Consolidation"),
    ("retrieval", "Retrieval"),
    ("limits", "Limits and quotas"),
    ("health", "Health scoring"),
    ("privacy", "Privacy and retention"),
)

RANKING_KEYS = ("similarity", "importance", "confidence", "recency", "relationship")
# Every factor the health score knows about, in the order the form should show them.
HEALTH_WEIGHT_KEYS = tuple(HEALTH_WEIGHTS)


def defaults() -> dict[str, Any]:
    settings = get_settings()
    return {
        "min_event_importance": settings.memory_min_event_importance,
        "consolidation_similarity": settings.memory_consolidation_similarity,
        "decay_days": settings.memory_default_decay_days,
        "context_token_budget": settings.memory_context_token_budget,
        "rate_limit_per_minute": 600,
        "monthly_event_quota": 0,
        "pii_redaction_enabled": settings.pii_redaction_enabled,
        "health_weights": dict(HEALTH_WEIGHTS),
        "restriction_policies": [],
        "ranking_weights": {
            "similarity": settings.rank_weight_similarity,
            "importance": settings.rank_weight_importance,
            "confidence": settings.rank_weight_confidence,
            "recency": settings.rank_weight_recency,
            "relationship": settings.rank_weight_relationship,
        },
        "retention": {
            "events_days": settings.retention_event_days,
            "memories_days": settings.retention_memory_days,
            "conversations_days": settings.retention_conversation_days,
        },
        "event_importance": {},
    }


def schema() -> list[SettingField]:
    """Every tunable, with the bounds the writer enforces."""
    values = defaults()
    return [
        SettingField(
            key="min_event_importance",
            label="Minimum event importance",
            group="extraction",
            kind="percent",
            default=values["min_event_importance"],
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            help=(
                "Events scoring below this are stored but never extracted. Raise it to keep "
                "memory sparse; lower it to remember more. The Event Explorer shows each "
                "event's score so you can tune against real traffic."
            ),
        ),
        SettingField(
            key="event_importance",
            label="Per-event-type importance",
            group="extraction",
            kind="map",
            default=values["event_importance"],
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            help=(
                "Overrides the built-in importance table for your own event types. "
                "A payload field {\"importance\": 0.9} overrides both for a single event."
            ),
        ),
        SettingField(
            key="consolidation_similarity",
            label="Consolidation threshold",
            group="consolidation",
            kind="percent",
            default=values["consolidation_similarity"],
            minimum=0.1,
            maximum=0.95,
            step=0.01,
            help=(
                "How similar two statements must be before they merge into one memory. "
                "Calibrated for the local lexical embedder: restatements land around "
                "0.45-0.65, unrelated statements below 0.15. Below ~0.3 distinct problems "
                "start merging."
            ),
        ),
        SettingField(
            key="decay_days",
            label="Decay window",
            group="consolidation",
            kind="number",
            default=values["decay_days"],
            minimum=0,
            maximum=3650,
            step=1,
            unit="days",
            help=(
                "Base window for transient memory types. Durable types (fact, preference, "
                "relationship, subscription, feedback) never expire. 0 disables decay."
            ),
        ),
        SettingField(
            key="ranking_weights",
            label="Ranking weights",
            group="retrieval",
            kind="weights",
            default=values["ranking_weights"],
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            keys=RANKING_KEYS,
            help=(
                "How retrieved memories are ordered. Raise recency if answers feel stale; "
                "raise importance and confidence if they feel scattershot. Weights are "
                "normalised, so only their ratio matters."
            ),
        ),
        SettingField(
            key="health_weights",
            label="Health factor weights",
            group="health",
            kind="map",
            default=values["health_weights"],
            minimum=MIN_WEIGHT,
            maximum=MAX_WEIGHT,
            step=0.5,
            keys=HEALTH_WEIGHT_KEYS,
            help=(
                "How much each factor moves the health score, out of 100, from a baseline "
                "of 70. Negative pulls down. What counts as unhealthy differs by product — "
                "churn language may matter more to you than an open problem does."
            ),
        ),
        SettingField(
            key="context_token_budget",
            label="Context token budget",
            group="retrieval",
            kind="number",
            default=values["context_token_budget"],
            minimum=200,
            maximum=20000,
            step=100,
            unit="tokens",
            help=(
                "Default ceiling for /v1/memory/context. Callers can request less per "
                "request; the response is marked truncated rather than overflowing."
            ),
        ),
        SettingField(
            key="rate_limit_per_minute",
            label="Rate limit",
            group="limits",
            kind="number",
            default=values["rate_limit_per_minute"],
            minimum=1,
            maximum=100000,
            step=1,
            unit="req/min",
            help="Per-project request ceiling. Responses carry X-RateLimit-* headers.",
        ),
        SettingField(
            key="monthly_event_quota",
            label="Monthly event quota",
            group="limits",
            kind="number",
            default=values["monthly_event_quota"],
            minimum=0,
            maximum=100000000,
            step=1000,
            unit="events",
            help="Billing allowance, enforced from this project's own usage counters. 0 disables it.",
        ),
        SettingField(
            key="restriction_policies",
            label="Restricted memory",
            group="privacy",
            kind="policies",
            default=values["restriction_policies"],
            help=(
                "Rules that mark a memory as restricted when it is written. A restricted "
                "memory is only readable with the memory:restricted scope, and never "
                "reaches an answer composed for a caller without it. Each rule is a type, "
                "a list of terms, or a regular expression."
            ),
            # The memory types, so the editor can offer them rather than have an operator
            # guess a name that the writer would then reject.
            keys=[memory_type.value for memory_type in MemoryType],
        ),
        SettingField(
            key="pii_redaction_enabled",
            label="Redact PII before extraction",
            group="privacy",
            kind="boolean",
            default=values["pii_redaction_enabled"],
            help=(
                "Emails, phone numbers, card-like numbers and secrets are scrubbed from the "
                "text used for extraction. The immutable event keeps what the customer sent."
            ),
        ),
        SettingField(
            key="retention",
            label="Retention windows",
            group="privacy",
            kind="map",
            default=values["retention"],
            minimum=0,
            maximum=3650,
            step=1,
            unit="days",
            keys=("events_days", "memories_days", "conversations_days"),
            help="How long raw events, memories and query traces are kept. 0 means forever.",
        ),
    ]


SCHEMA_BY_KEY: dict[str, SettingField] = {field.key: field for field in schema()}


def effective(project: Project) -> dict[str, Any]:
    """Stored settings layered over the deployment defaults."""
    values = defaults()
    stored = project.settings or {}
    for key, value in stored.items():
        if key in values and isinstance(values[key], dict) and isinstance(value, dict):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    return values


def validate(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a settings patch.

    Unknown keys pass through untouched — a newer engine may understand settings this
    version does not, and silently dropping them would be worse than storing them.
    """
    if not isinstance(patch, dict):
        raise ValidationError("Settings must be an object.")

    cleaned: dict[str, Any] = {}
    for key, value in patch.items():
        definition = SCHEMA_BY_KEY.get(key)
        if definition is None:
            cleaned[key] = value
            continue
        cleaned[key] = _validate_field(definition, value)

    _check_cross_field_rules(cleaned)
    if "restriction_policies" in cleaned:
        cleaned["restriction_policies"] = _validate_policies(cleaned["restriction_policies"])
    if "integrations" in cleaned:
        cleaned["integrations"] = _encrypt_provider_secrets(cleaned["integrations"])
    return cleaned


def _validate_policies(raw: Any) -> Any:
    """Compile the policy so a broken rule is refused here, not on every later read.

    A bad regular expression stored in settings would otherwise turn every memory write
    into an exception, long after whoever typed it had moved on.
    """
    try:
        compiled = compile_policy(raw)
    except PolicyError as exc:
        raise ValidationError(str(exc)) from exc
    return [rule.as_dict() for rule in compiled.rules]


def _encrypt_provider_secrets(integrations: Any) -> Any:
    """Encrypt inbound signing secrets on the way in.

    A provider's signing secret has to be kept in full — verification needs the value —
    so it is encrypted here rather than hashed. Doing it in ``validate`` means every write
    path is covered: there is no way to store one of these in plaintext through the API.
    Values that are already ciphertext are left alone, so a settings round trip does not
    double-encrypt them.
    """
    if not isinstance(integrations, dict):
        return integrations

    keys = get_settings().encryption_keys
    if not keys:
        return integrations

    result: dict[str, Any] = {}
    for provider, config in integrations.items():
        if not isinstance(config, dict):
            result[provider] = config
            continue
        secret = config.get("signing_secret")
        if secret and not is_encrypted(str(secret)):
            config = {**config, "signing_secret": encrypt(str(secret), keys[0])}
        result[provider] = config
    return result


def _validate_field(definition: SettingField, value: Any) -> Any:
    if definition.kind == "boolean":
        if not isinstance(value, bool):
            raise ValidationError(f"{definition.label} must be true or false.")
        return value

    if definition.kind in ("number", "percent"):
        return _number(definition, value, definition.label)

    if definition.kind == "weights":
        if not isinstance(value, dict):
            raise ValidationError(f"{definition.label} must be an object.")
        unknown = set(value) - set(definition.keys)
        if unknown:
            raise ValidationError(
                f"{definition.label} has unknown keys: {', '.join(sorted(unknown))}."
            )
        weights = {
            key: _number(definition, weight, f"{definition.label}.{key}")
            for key, weight in value.items()
        }
        if weights and sum(weights.values()) <= 0:
            raise ValidationError("Ranking weights cannot all be zero.")
        return weights

    if definition.kind == "map":
        if not isinstance(value, dict):
            raise ValidationError(f"{definition.label} must be an object.")
        if definition.keys:
            unknown = set(value) - set(definition.keys)
            if unknown:
                raise ValidationError(
                    f"{definition.label} has unknown keys: {', '.join(sorted(unknown))}."
                )
        entries: dict[str, Any] = {}
        for key, entry in value.items():
            name = str(key).strip()
            if not name:
                raise ValidationError(f"{definition.label} has an entry with an empty key.")
            if len(name) > 128:
                raise ValidationError(f"{definition.label} key '{name[:20]}…' is too long.")
            entries[name] = _number(definition, entry, f"{definition.label}.{name}")
        return entries

    return value


def _number(definition: SettingField, value: Any, label: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a number.")
    number = float(value)
    if definition.minimum is not None and number < definition.minimum:
        raise ValidationError(f"{label} must be at least {_pretty(definition.minimum)}.")
    if definition.maximum is not None and number > definition.maximum:
        raise ValidationError(f"{label} must be at most {_pretty(definition.maximum)}.")
    # Whole-number settings stay integers so the stored JSON reads like the form.
    if definition.step is not None and definition.step >= 1:
        return int(round(number))
    return round(number, 4)


def _pretty(value: float) -> str:
    """Bounds read better as "600" than "600.0" in an error message."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _check_cross_field_rules(values: dict[str, Any]) -> None:
    """Rules that only make sense across two settings."""
    quota = values.get("monthly_event_quota")
    rate = values.get("rate_limit_per_minute")
    if isinstance(quota, int) and isinstance(rate, int) and quota and quota < rate:
        raise ValidationError(
            "Monthly event quota is lower than one minute of the rate limit, which would "
            "make the rate limit meaningless."
        )


def known_memory_types() -> list[str]:
    return [memory_type.value for memory_type in MemoryType]
