"""What a memory is about: the products, integrations and features it names.

Extraction records every entity a statement names — "Shopify", but also "The CSV" (a phrase
the recogniser took for a name) and "ts-support" (a Slack channel it filed as a person). A
topic is what a person would group by: the entity graph's products, integrations and
features. Shared by "what changed" (§26 4.1), the brief's "they care about" (§26 5.2) and
the journey (§26 6.6), so all three name the same things.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TOPIC_TYPES = frozenset({"integration", "product", "feature"})
# Attributes a template records naming what the memory is about ("uses the X integration").
_ATTRIBUTES = ("integration", "feature", "product")


def normalized(name: Any) -> str:
    return " ".join(str(name or "").strip().lower().split())


def topics_of(
    memory: Any, types: Mapping[str, str] | None = None, *, exclude: str = "", limit: int = 3
) -> list[str]:
    """The topics a memory names, in the order it names them — by the types the entity graph
    gave them (``types``: normalised name → entity type), or every name when no types are
    known. What a template recorded (``integration``, ``feature``) counts either way."""
    meta = getattr(memory, "meta", None)
    meta = meta if isinstance(meta, dict) else {}
    skip = normalized(exclude)
    found: dict[str, str] = {}
    for name in meta.get("entity_names") or []:
        key = normalized(name)
        if not key or key == skip or key in found:
            continue
        if types and types.get(key) not in TOPIC_TYPES:
            continue
        found[key] = str(name).strip()
    for attribute in _ATTRIBUTES:
        value = meta.get(attribute)
        key = normalized(value)
        if key and key != skip and key not in found:
            found[key] = str(value).strip()
    return list(found.values())[:limit]
