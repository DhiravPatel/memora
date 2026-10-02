"""Memory contracts: what each event type must look like (§26 7.1).

The biggest integration problem is rarely retrieval — it is events sent in the wrong shape:
``{"amount": "₹500"}`` where a number was meant, a ``reason`` that is sometimes ``note``,
a ``customer_id`` buried in a nested object. A contract per event type says what the
payload must contain — required fields, each field's type and bounds — which field carries
the human-written text, and how important the event type is:

.. code-block:: yaml

    event: payment_failed
    required: [amount, currency]
    fields:
      amount:   {type: number, minimum: 0}
      currency: {type: string, enum: [USD, EUR, INR]}
      reason:   {type: string}
    text_field: reason
    importance: 0.9

Checked on ingest in a **mode**: ``warn`` stores the event with its violations, ``enforce``
refuses it, ``off`` checks nothing but still applies the text field and importance. Paths
are dotted into ``data`` (``ticket.priority``); ``customer_id`` is the envelope's and is
always required.

Everything here is pure: compiling, validating, inferring a draft from real traffic.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.pii import redact_text

MODES = ("warn", "enforce", "off")
FIELD_TYPES = ("string", "number", "integer", "boolean", "object", "array", "timestamp", "email", "url", "any")
EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}(\.[A-Za-z_][A-Za-z0-9_-]{0,63}){0,5}$")
MAX_FIELDS = 100
MAX_ENUM = 100
MAX_PATTERN = 300
MAX_DESCRIPTION = 500
# Envelope fields a contract may name as required; the envelope always has them.
ENVELOPE = frozenset({"customer_id", "event_type", "occurred_at"})
PREVIEW = 40
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


class ContractError(ValueError):
    """A contract that cannot be compiled — refused when saved, never at ingest."""


@dataclass(frozen=True, slots=True)
class FieldRule:
    path: str
    type: str = "any"
    required: bool = False
    enum: tuple[Any, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    pattern: re.Pattern[str] | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        rule: dict[str, Any] = {"type": self.type}
        if self.required:
            rule["required"] = True
        if self.enum is not None:
            rule["enum"] = list(self.enum)
        for name in ("minimum", "maximum", "max_length", "description"):
            value = getattr(self, name)
            if value is not None:
                rule[name] = value
        if self.pattern is not None:
            rule["pattern"] = self.pattern.pattern
        return rule


@dataclass(frozen=True, slots=True)
class Contract:
    event_type: str
    mode: str = "warn"
    fields: tuple[FieldRule, ...] = ()
    text_field: str | None = None
    importance: float | None = None
    allow_extra: bool = True
    description: str = ""

    @property
    def checks(self) -> bool:
        return self.mode != "off"

    def field(self, path: str) -> FieldRule | None:
        return next((rule for rule in self.fields if rule.path == path), None)

    def as_dict(self) -> dict[str, Any]:
        """The canonical stored form."""
        return {
            "event_type": self.event_type,
            "mode": self.mode,
            "description": self.description,
            "required": [rule.path for rule in self.fields if rule.required],
            "fields": {rule.path: rule.as_dict() for rule in self.fields},
            "text_field": self.text_field,
            "importance": self.importance,
            "allow_extra": self.allow_extra,
        }


@dataclass(frozen=True, slots=True)
class Violation:
    path: str
    rule: str  # required | type | enum | minimum | maximum | max_length | pattern | unknown_field
    expected: str
    received: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "rule": self.rule,
            "expected": self.expected,
            "received": self.received,
            "message": self.message,
        }


# ------------------------------------------------------------------ compile


def _number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(f"{where} must be a number.")
    return float(value)


def compile_contract(raw: Any, *, event_type: str | None = None) -> Contract:
    """Validate a contract. Accepts the YAML-shaped form: a top-level ``required`` list of
    paths and a ``fields`` map — or ``required: true`` on a field."""
    if not isinstance(raw, dict):
        raise ContractError("A contract is an object: event, required, fields, text_field, importance, mode.")
    name = str(raw.get("event_type") or raw.get("event") or event_type or "").strip().lower().replace(" ", "_")
    if not EVENT_TYPE.match(name):
        raise ContractError(f"{name!r} is not a valid event type — lowercase letters, digits and _ . : -")
    if event_type is not None and name != event_type:
        raise ContractError(f"This contract is for {event_type!r}, not {name!r}.")
    mode = str(raw.get("mode") or "warn").strip().lower()
    if mode not in MODES:
        raise ContractError(f"'mode' is one of {', '.join(MODES)}.")

    declared = raw.get("fields") or {}
    if isinstance(declared, list):  # [{"path": …, "type": …}, …] is accepted too
        declared = {str(item.get("path")): item for item in declared if isinstance(item, dict)}
    if not isinstance(declared, dict):
        raise ContractError("'fields' is an object of path → {type, …}.")
    if len(declared) > MAX_FIELDS:
        raise ContractError(f"At most {MAX_FIELDS} fields.")
    required = raw.get("required") or []
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ContractError("'required' is a list of field paths.")
    required_paths = {item.strip() for item in required if item.strip() and item.strip() not in ENVELOPE}

    rules: list[FieldRule] = []
    for path, spec in declared.items():
        path = str(path).strip()
        if not PATH.match(path):
            raise ContractError(f"{path!r} is not a valid field path — dotted names, e.g. 'amount' or 'ticket.priority'.")
        spec = spec if isinstance(spec, dict) else {"type": spec}
        kind = str(spec.get("type") or "any").strip().lower()
        if kind not in FIELD_TYPES:
            raise ContractError(f"Field {path!r}: type is one of {', '.join(FIELD_TYPES)}.")
        enum = spec.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not enum or len(enum) > MAX_ENUM:
                raise ContractError(f"Field {path!r}: 'enum' is a list of 1 to {MAX_ENUM} values.")
            enum = tuple(enum)
        minimum = _number(spec.get("minimum"), f"Field {path!r}: 'minimum'")
        maximum = _number(spec.get("maximum"), f"Field {path!r}: 'maximum'")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ContractError(f"Field {path!r}: 'minimum' is greater than 'maximum'.")
        if (minimum is not None or maximum is not None) and kind not in ("number", "integer", "any"):
            raise ContractError(f"Field {path!r}: 'minimum' and 'maximum' apply to numbers.")
        max_length = spec.get("max_length")
        if max_length is not None and (isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1):
            raise ContractError(f"Field {path!r}: 'max_length' is a whole number of at least 1.")
        pattern = spec.get("pattern")
        compiled = None
        if pattern is not None:
            if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN:
                raise ContractError(f"Field {path!r}: 'pattern' is a regular expression of at most {MAX_PATTERN} characters.")
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ContractError(f"Field {path!r}: 'pattern' is not a valid regular expression ({exc}).") from exc
        description = spec.get("description")
        if description is not None and len(str(description)) > MAX_DESCRIPTION:
            raise ContractError(f"Field {path!r}: the description is longer than {MAX_DESCRIPTION} characters.")
        rules.append(
            FieldRule(
                path=path,
                type=kind,
                required=bool(spec.get("required")) or path in required_paths,
                enum=enum,
                minimum=minimum,
                maximum=maximum,
                max_length=max_length,
                pattern=compiled,
                description=str(description) if description else None,
            )
        )
    declared_paths = {rule.path for rule in rules}
    for path in sorted(required_paths - declared_paths):
        if not PATH.match(path):
            raise ContractError(f"{path!r} is not a valid field path.")
        rules.append(FieldRule(path=path, required=True))

    text_field = raw.get("text_field")
    if text_field is not None:
        text_field = str(text_field).strip() or None
    if text_field is not None:
        if not PATH.match(text_field):
            raise ContractError(f"'text_field' {text_field!r} is not a valid field path.")
        declared_text = next((rule for rule in rules if rule.path == text_field), None)
        if declared_text is not None and declared_text.type not in ("string", "any"):
            raise ContractError(f"'text_field' {text_field!r} is declared as {declared_text.type}; the text is a string.")
    importance = raw.get("importance")
    if importance is not None:
        importance = _number(importance, "'importance'")
        if not 0.0 <= importance <= 1.0:
            raise ContractError("'importance' is between 0 and 1.")
    description = str(raw.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION:
        raise ContractError(f"The description is longer than {MAX_DESCRIPTION} characters.")
    return Contract(
        event_type=name,
        mode=mode,
        fields=tuple(rules),
        text_field=text_field,
        importance=importance,
        allow_extra=raw.get("allow_extra", True) is not False,
        description=description,
    )


# ------------------------------------------------------------------ validate


def type_of(value: Any) -> str:
    """The JSON type of a value, as the contract language names types."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list | tuple):
        return "array"
    return type(value).__name__


def preview(value: Any) -> str:
    """What was received, short and redacted: `string ("₹500")`, `null`, `object`."""
    kind = type_of(value)
    if kind in ("null", "object", "array"):
        return kind
    text = redact_text(str(value))
    if len(text) > PREVIEW:
        text = text[: PREVIEW - 1] + "…"
    return f'{kind} ("{text}")' if kind == "string" else f"{kind} ({text})"


_MISSING = object()


def at_path(data: Any, path: str) -> Any:
    """The value at a dotted path, or a sentinel when any step is missing."""
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def value_at(data: Any, path: str) -> Any:
    """The value at a dotted path, or ``None``."""
    found = at_path(data, path)
    return None if found is _MISSING else found


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _matches(kind: str, value: Any) -> bool:
    if kind == "any":
        return True
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "timestamp":
        return _timestamp(value)
    if kind == "email":
        return isinstance(value, str) and bool(_EMAIL.match(value))
    if kind == "url":
        return isinstance(value, str) and bool(_URL.match(value))
    return False


def _paths(data: Any, prefix: str = "") -> Iterable[str]:
    for key, value in (data or {}).items():
        path = f"{prefix}{key}"
        yield path
        if isinstance(value, dict):
            yield from _paths(value, f"{path}.")


def validate(contract: Contract, data: dict[str, Any] | None) -> list[Violation]:
    """Every way the payload breaks the contract — not just the first."""
    data = data or {}
    found: list[Violation] = []
    for rule in contract.fields:
        value = at_path(data, rule.path)
        if value is _MISSING or value is None:
            if rule.required:
                found.append(
                    Violation(
                        rule.path,
                        "required",
                        "present",
                        "missing" if value is _MISSING else "null",
                        f"'{rule.path}' is required.",
                    )
                )
            continue
        if not _matches(rule.type, value):
            found.append(
                Violation(
                    rule.path,
                    "type",
                    rule.type,
                    preview(value),
                    f"'{rule.path}' should be {_article(rule.type)} {rule.type}; received {preview(value)}.",
                )
            )
            continue
        if rule.enum is not None and value not in rule.enum:
            allowed = ", ".join(str(item) for item in rule.enum[:10]) + ("…" if len(rule.enum) > 10 else "")
            found.append(
                Violation(rule.path, "enum", f"one of {allowed}", preview(value), f"'{rule.path}' should be one of {allowed}.")
            )
        if isinstance(value, int | float) and not isinstance(value, bool):
            if rule.minimum is not None and value < rule.minimum:
                found.append(
                    Violation(rule.path, "minimum", f">= {_pretty(rule.minimum)}", preview(value), f"'{rule.path}' is below {_pretty(rule.minimum)}.")
                )
            if rule.maximum is not None and value > rule.maximum:
                found.append(
                    Violation(rule.path, "maximum", f"<= {_pretty(rule.maximum)}", preview(value), f"'{rule.path}' is above {_pretty(rule.maximum)}.")
                )
        if isinstance(value, str):
            if rule.max_length is not None and len(value) > rule.max_length:
                found.append(
                    Violation(
                        rule.path,
                        "max_length",
                        f"at most {rule.max_length} characters",
                        f"{len(value)} characters",
                        f"'{rule.path}' is longer than {rule.max_length} characters.",
                    )
                )
            if rule.pattern is not None and not rule.pattern.search(value):
                found.append(
                    Violation(
                        rule.path, "pattern", f"matching {rule.pattern.pattern}", preview(value),
                        f"'{rule.path}' does not match {rule.pattern.pattern}.",
                    )
                )
    if not contract.allow_extra:
        known = {rule.path for rule in contract.fields}
        prefixes = {".".join(rule.path.split(".")[:depth]) for rule in contract.fields for depth in range(1, rule.path.count(".") + 1)}
        for path in _paths(data):
            # ``importance`` is the one payload field the platform itself reads.
            if (
                path not in known
                and path not in prefixes
                and path != "importance"
                and not any(path.startswith(f"{item}.") for item in known)
            ):
                found.append(
                    Violation(path, "unknown_field", "not present", preview(value_at(data, path)), f"'{path}' is not in the contract.")
                )
    return found


def _article(kind: str) -> str:
    return "an" if kind[:1] in "aeiou" else "a"


def _pretty(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def outcome(contract: Contract, violations: Sequence[Violation], *, version: int) -> dict[str, Any]:
    """What is stored on the event: the check, its result, and the contract version."""
    return {
        "version": version,
        "mode": contract.mode,
        "valid": not violations,
        "violations": [violation.as_dict() for violation in violations],
    }


# ------------------------------------------------------------------ infer


@dataclass(slots=True)
class _Seen:
    present: int = 0
    types: Counter[str] = field(default_factory=Counter)
    values: Counter[str] = field(default_factory=Counter)
    lengths: list[int] = field(default_factory=list)


_TEXT_NAMES = ("message", "text", "body", "comment", "feedback", "reason", "note", "notes", "content", "description", "subject")


def infer(event_type: str, samples: Sequence[dict[str, Any]], *, max_enum: int = 8) -> dict[str, Any]:
    """A draft contract from real payloads: a field present in every sample is required, its
    type is the one it always has, a string with a handful of distinct values is an enum,
    and the text field is the longest free-text field. A field sent as different types is
    ``any``, with ``seen`` counting each type. A draft, to be read and saved."""
    seen: dict[str, _Seen] = {}
    for sample in samples:
        for path in _paths(sample or {}):
            value = value_at(sample, path)
            entry = seen.setdefault(path, _Seen())
            entry.present += 1
            entry.types[_inferred_type(value)] += 1
            if isinstance(value, str):
                entry.values[value] += 1
                entry.lengths.append(len(value))
    total = len(samples)
    fields: dict[str, Any] = {}
    for path, entry in sorted(seen.items()):
        if path == "importance":
            continue
        kind = _dominant(entry.types)
        rule: dict[str, Any] = {"type": kind}
        if total and entry.present == total:
            rule["required"] = True
        sent_as = {name: count for name, count in entry.types.most_common() if name != "null"}
        if kind == "any" and len(sent_as) > 1:
            # The traffic disagrees with itself — the thing a contract is for. Say how.
            rule["seen"] = sent_as
        # A short label with a handful of values is a category, not free text.
        categorical = (
            kind == "string"
            and total >= 5
            and 1 < len(entry.values) <= max_enum
            and sum(entry.values.values()) >= 5
            and max(entry.lengths or [0]) <= 40
            and not any(len(value.split()) > 3 for value in entry.values)
        )
        if categorical:
            rule["enum"] = sorted(entry.values)
        fields[path] = rule
    text_field = None
    candidates = [
        (sum(entry.lengths) / len(entry.lengths), path)
        for path, entry in seen.items()
        if entry.lengths and _dominant(entry.types) == "string" and path.split(".")[-1].lower() in _TEXT_NAMES
    ]
    if not candidates:
        candidates = [
            (sum(entry.lengths) / len(entry.lengths), path)
            for path, entry in seen.items()
            if entry.lengths and _dominant(entry.types) == "string" and sum(entry.lengths) / len(entry.lengths) >= 25
        ]
    if candidates:
        text_field = max(candidates)[1]
        fields.get(text_field, {}).pop("enum", None)
    return {
        "event_type": event_type,
        "mode": "warn",
        "required": sorted(path for path, rule in fields.items() if rule.get("required")),
        "fields": {path: {key: value for key, value in rule.items() if key != "required"} for path, rule in fields.items()},
        "text_field": text_field,
        "importance": None,
        "allow_extra": True,
        "description": f"Drafted from {total} recent {event_type} event{'s' if total != 1 else ''}.",
        "samples": total,
    }


def _inferred_type(value: Any) -> str:
    kind = type_of(value)
    if kind == "string":
        if _EMAIL.match(value):
            return "email"
        if _timestamp(value) and any(char.isdigit() for char in value) and len(value) >= 10:
            return "timestamp"
        if _URL.match(value):
            return "url"
    return kind


def _dominant(types: Counter[str]) -> str:
    kinds = {kind for kind in types if kind != "null"}
    if not kinds:
        return "any"
    if kinds <= {"integer", "number"}:
        return "integer" if kinds == {"integer"} else "number"
    if len(kinds) == 1:
        return next(iter(kinds))
    if kinds <= {"string", "email", "timestamp", "url"}:
        return "string"
    return "any"


__all__ = [
    "FIELD_TYPES",
    "MODES",
    "Contract",
    "ContractError",
    "FieldRule",
    "Violation",
    "at_path",
    "compile_contract",
    "infer",
    "outcome",
    "preview",
    "type_of",
    "validate",
    "value_at",
]
