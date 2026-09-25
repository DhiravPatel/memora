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
from memory_engine.drift import DriftSettings
from memory_engine.freshness import DEFAULT_WINDOWS as FRESHNESS_WINDOWS
from memory_engine.guardrails import BUILTIN_RULES, GuardrailError, compile_guardrails
from memory_engine.lifecycle import (
    DEFAULT_LIFECYCLE,
    TRACK_TEMPLATES,
    LifecycleError,
    compile_lifecycle,
    compile_tracks,
)
from memory_engine.policy import PolicyError, compile_policy

FieldKind = Literal[
    "number",
    "percent",
    "boolean",
    "map",
    "weights",
    "text",
    "policies",
    "lifecycle",
    "lifecycle_tracks",
    "guardrails",
]

# How long a person has to answer an agent's request before it lapses, by default.
DEFAULT_APPROVAL_TTL_HOURS = 24
MAX_APPROVAL_TTL_HOURS = 720


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
    ("lifecycle", "Lifecycle"),
    ("agents", "Agents"),
    ("freshness", "Freshness and drift"),
)

RANKING_KEYS = ("similarity", "importance", "confidence", "recency", "relationship")
DRIFT_DEFAULTS = DriftSettings()
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
        "lifecycle": DEFAULT_LIFECYCLE,
        # Extra tracks are opt-in for existing projects; new projects start with the
        # engagement and commercial templates (see ``new_project_settings``).
        "lifecycle_tracks": {},
        "concept_retrieval": True,
        "keyword_match_any": True,
        "guardrails": {"disabled": [], "rules": [], "auto_approve": [], "approval_ttl_hours": DEFAULT_APPROVAL_TTL_HOURS},
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
        "freshness_days": dict(FRESHNESS_WINDOWS),
        "drift_min_contacts": DRIFT_DEFAULTS.min_contacts,
        "drift_min_share": DRIFT_DEFAULTS.min_share,
        "drift_min_billing_events": DRIFT_DEFAULTS.min_billing_events,
        "drift_quiet_days": {"problem": DRIFT_DEFAULTS.quiet_problem_days, "usage": DRIFT_DEFAULTS.quiet_usage_days},
        "drift_min_activity": DRIFT_DEFAULTS.min_activity,
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
            key="keyword_match_any",
            label="Keyword search matches any word",
            group="retrieval",
            kind="boolean",
            default=values["keyword_match_any"],
            help=(
                "Match a question on any of its meaningful words, ranked by how many match — "
                "\"are the webhook retries still failing?\" finds \"the webhook retries are "
                "failing again\". Off, every word must appear. Queries using search syntax "
                "(quotes, -term, or) always keep it. Measure a change with an evaluation run."
            ),
        ),
        SettingField(
            key="concept_retrieval",
            label="Concept retrieval",
            group="retrieval",
            kind="boolean",
            default=values["concept_retrieval"],
            help=(
                "Also find memories that are about what was asked in different words — "
                "\"the connector stopped functioning\" for a question about the integration "
                "being broken. Recall only: a concept match ranks below an exact lexical "
                "match of the same strength. Measure it with an evaluation run."
            ),
        ),
        SettingField(
            key="lifecycle",
            label="Lifecycle states",
            group="lifecycle",
            kind="lifecycle",
            default=values["lifecycle"],
            help=(
                "The states a customer moves through and the rules that move them, written "
                "in the condition language. Transitions are tried in order and the first "
                "that matches wins. A state set by hand is pinned and left alone until "
                "released. Set enabled to false to stop tracking lifecycle altogether."
            ),
        ),
        SettingField(
            key="lifecycle_tracks",
            label="Lifecycle tracks",
            group="lifecycle",
            kind="lifecycle_tracks",
            default=values["lifecycle_tracks"],
            keys=tuple(TRACK_TEMPLATES),
            help=(
                "More state machines beside the primary lifecycle — say, how engaged a customer "
                "is and where they are commercially. Each is written like the lifecycle, has "
                "its own history and its own `lifecycle.<track>` fact, and sends "
                "customer.state_changed with its track name. Start from a template."
            ),
        ),
        SettingField(
            key="guardrails",
            label="Agent guardrails",
            group="agents",
            kind="guardrails",
            default=values["guardrails"],
            keys=tuple(BUILTIN_RULES),
            help=(
                "What an agent may do to a customer, decided by POST /v1/agent/actions/request "
                "(or /v1/agent/check) before it acts. Built-in rules — opt-outs included — can "
                "be switched off; project rules are conditions over customer facts, the "
                "customer's action history (actions.*) and request.* (action, channel, amount, "
                "topic, plan, reply), each denying or requiring a person's approval. Automatic "
                "limits approve money and account actions within an amount and a monthly count. "
                "Approvals lapse after the hours set."
            ),
        ),
        SettingField(
            key="freshness_days",
            label="Freshness windows",
            group="freshness",
            kind="map",
            default=values["freshness_days"],
            minimum=1,
            maximum=3650,
            step=1,
            unit="days",
            keys=tuple(FRESHNESS_WINDOWS),
            help=(
                "Days without new evidence — the customer saying it again, or a person "
                "confirming it — after which a memory of each type is stale; it is aging from "
                "half that. Effective confidence halves every window. Stale memories are marked "
                "in context, the brief and the dashboard; nothing is deleted."
            ),
        ),
        SettingField(
            key="drift_min_contacts",
            label="Channel drift: contacts",
            group="freshness",
            kind="number",
            default=values["drift_min_contacts"],
            minimum=2,
            maximum=100,
            step=1,
            unit="contacts",
            help=(
                "Contacts on another channel, since a customer said which they prefer, before "
                "the preference is flagged possibly outdated. Only the customer reaching out "
                "counts, never what you sent."
            ),
        ),
        SettingField(
            key="drift_min_share",
            label="Channel drift: share",
            group="freshness",
            kind="percent",
            default=values["drift_min_share"],
            minimum=0.5,
            maximum=1,
            step=0.05,
            help="The share of their contacts since that must come through that other channel.",
        ),
        SettingField(
            key="drift_min_billing_events",
            label="Plan drift: billing events",
            group="freshness",
            kind="number",
            default=values["drift_min_billing_events"],
            minimum=1,
            maximum=12,
            step=1,
            unit="events",
            help=(
                "Consecutive billing events naming another plan before the remembered plan is "
                "flagged possibly outdated."
            ),
        ),
        SettingField(
            key="drift_quiet_days",
            label="Quiet windows",
            group="freshness",
            kind="map",
            default=values["drift_quiet_days"],
            minimum=7,
            maximum=365,
            step=1,
            unit="days",
            keys=("problem", "usage"),
            help=(
                "How long an open problem can go unreported — or a feature they said they use "
                "go unused — while the customer stays active, before it is flagged: the problem "
                "may have been fixed, the habit may have changed."
            ),
        ),
        SettingField(
            key="drift_min_activity",
            label="Stayed active: events",
            group="freshness",
            kind="number",
            default=values["drift_min_activity"],
            minimum=1,
            maximum=100,
            step=1,
            unit="events",
            help=(
                "Events in a quiet window that show the customer stayed active. A customer who "
                "went quiet altogether is silent, not changed, and is left to the forecast."
            ),
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
    if "lifecycle" in cleaned:
        cleaned["lifecycle"] = _validate_lifecycle(cleaned["lifecycle"])
    if "guardrails" in cleaned:
        cleaned["guardrails"] = validate_guardrails(cleaned["guardrails"])
    if "lifecycle_tracks" in cleaned:
        cleaned["lifecycle_tracks"] = validate_tracks(cleaned["lifecycle_tracks"])
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


def _validate_lifecycle(raw: Any) -> Any:
    """Compile the machine and store its canonical form.

    Every ``when`` is validated against the fact catalog here, so a typo in a transition is
    a 422 when the machine is saved rather than a customer who silently never moves.
    """
    if isinstance(raw, dict) and raw.get("enabled") is False:
        return {**raw, "enabled": False}
    try:
        machine = compile_lifecycle(raw)
    except LifecycleError as exc:
        raise ValidationError(str(exc)) from exc
    return {"enabled": True, **machine.as_dict()}


def validate_tracks(raw: Any) -> dict[str, Any]:
    """Compile every track now: a typo in a transition is a 422 when saved, not a customer
    who silently never moves."""
    try:
        tracks = compile_tracks(raw or {})
    except LifecycleError as exc:
        raise ValidationError(str(exc)) from exc
    return {name: track.as_dict() for name, track in tracks.items()}


def new_project_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a project is created with: the shipped tracks, unless the caller chose."""
    initial = {"lifecycle_tracks": validate_tracks(TRACK_TEMPLATES)}
    if settings:
        initial.update(validate(settings))
    return initial


def validate_guardrails(raw: Any) -> dict[str, Any]:
    """Compile every rule's condition now, so a typo is a 422 when the rules are saved —
    not an agent that is never stopped because its rule could not be read."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError(
            "Guardrails must be an object with 'disabled', 'rules', 'auto_approve' and 'approval_ttl_hours'."
        )
    unknown = set(raw) - {"disabled", "rules", "auto_approve", "approval_ttl_hours"}
    if unknown:
        raise ValidationError(f"Guardrails has unknown keys: {', '.join(sorted(unknown))}.")
    try:
        compiled = compile_guardrails(raw)
    except GuardrailError as exc:
        raise ValidationError(str(exc)) from exc
    ttl = raw.get("approval_ttl_hours", DEFAULT_APPROVAL_TTL_HOURS)
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 1 <= ttl <= MAX_APPROVAL_TTL_HOURS:
        raise ValidationError(f"approval_ttl_hours must be between 1 and {MAX_APPROVAL_TTL_HOURS}.")
    return {**compiled.as_dict(), "approval_ttl_hours": int(ttl)}


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
