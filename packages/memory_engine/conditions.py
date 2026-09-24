"""A small language for stating conditions about a customer.

Written by people, stored as JSON, evaluated against :class:`CustomerFacts`::

    health.score < 60 and problems.entities contains "shopify"
    signals.trajectory == "declining" or intents.kinds contains "cancellation"
    not (subscription.plan in ["enterprise", "business"])
    problems.oldest_open_days between 7 and 30
    customer.metadata.segment == "smb" and preferences.channel is set

The text form is for humans and is parsed into a canonical JSON tree
(``{"all": [...]}``, ``{"any": [...]}``, ``{"not": ...}``, and leaves
``{"fact", "op", "value"}``), which is what gets stored. Either form is accepted wherever
a condition is written; both render back to the same canonical text.

Design points that are easy to get wrong and are deliberate here:

* **Validation at write time.** An unknown fact, an operator that makes no sense for the
  fact's type (``health.score contains "x"``) or an enum value that does not exist
  (``health.band == "at-risk"``) is refused when the rule is saved — with a suggestion —
  rather than evaluating to false forever afterwards.
* **Three-valued logic.** A fact with no value (no subscription recorded yet) is *unknown*,
  not false. Kleene logic keeps ``not (plan == "enterprise")`` from turning "we don't know
  the plan" into "definitely not enterprise". An unknown result is treated as false by the
  caller, and the trace says it was unknown, so nobody debugs a rule that "should have
  fired" without being told why it could not.
* **Every evaluation explains itself.** The result carries the leaves that decided it — in
  the direction they decided it, through any ``not`` — and the evidence ids behind each,
  so a guardrail can say *which* memories blocked an upsell.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from memory_engine.facts import CATALOG, METADATA_PREFIX, REQUEST_PREFIX, CustomerFacts, FactSpec
from memory_engine.policy import WITHHELD
from nlp.tokenize import root, surface_words, tokenize

MAX_DEPTH = 8
MAX_LEAVES = 50
MAX_TEXT_LENGTH = 2000
MAX_LIST_VALUES = 50

OPERATORS = (
    "eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in",
    "contains", "not_contains", "exists", "not_exists", "between",
)

# Aliases accepted in JSON as well as text, so ``{"op": "<"}`` is not an error.
OP_ALIASES = {
    "==": "eq", "=": "eq", "is": "eq", "!=": "ne", "<>": "ne",
    "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
    "not in": "not_in", "notin": "not_in", "not contains": "not_contains",
    "is set": "exists", "is not set": "not_exists", "missing": "not_exists",
}

OPS_FOR_TYPE: dict[str, frozenset[str]] = {
    "number": frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "between", "exists", "not_exists"}),
    "string": frozenset({"eq", "ne", "in", "not_in", "contains", "not_contains", "exists", "not_exists"}),
    "enum": frozenset({"eq", "ne", "in", "not_in", "exists", "not_exists"}),
    "boolean": frozenset({"eq", "ne", "exists", "not_exists"}),
    "list": frozenset({"contains", "not_contains", "exists", "not_exists"}),
    "terms": frozenset({"contains", "not_contains", "exists", "not_exists"}),
    "any": frozenset(OPERATORS),
}

_TEXT_FOR_OP = {
    "eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
    "in": "in", "not_in": "not in", "contains": "contains", "not_contains": "not contains",
    "exists": "is set", "not_exists": "is not set", "between": "between",
}


class ConditionError(ValueError):
    """A condition that cannot be parsed or does not make sense for the facts it names."""

    def __init__(self, message: str, *, position: int | None = None) -> None:
        super().__init__(message)
        self.position = position


# --------------------------------------------------------------------- tokens

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<op><=|>=|==|!=|<>|&&|\|\||[<>=!(),\[\]])
  | (?P<word>[A-Za-z_][A-Za-z0-9_.\-]*)
    """,
    re.VERBOSE,
)


@dataclass(slots=True)
class _Token:
    kind: str  # string | number | op | word | end
    text: str
    position: int

    @property
    def lower(self) -> str:
        return self.text.lower()


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise ConditionError(f"Unexpected character {text[position]!r}.", position=position)
        kind = match.lastgroup or ""
        if kind != "space":
            tokens.append(_Token(kind, match.group(), position))
        position = match.end()
    tokens.append(_Token("end", "", len(text)))
    return tokens


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


# --------------------------------------------------------------------- parser


class _Parser:
    """Recursive descent: ``or`` binds loosest, then ``and``, then ``not``."""

    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def _advance(self) -> _Token:
        token = self.tokens[self.index]
        self.index = min(self.index + 1, len(self.tokens) - 1)
        return token

    def _is_word(self, *words: str) -> bool:
        return self.current.kind == "word" and self.current.lower in words

    def _is_op(self, *ops: str) -> bool:
        return self.current.kind == "op" and self.current.text in ops

    def parse(self) -> dict[str, Any]:
        if self.current.kind == "end":
            raise ConditionError("The condition is empty.", position=0)
        node = self._or()
        if self.current.kind != "end":
            raise ConditionError(
                f"Unexpected {self.current.text!r} — expected 'and', 'or' or the end.",
                position=self.current.position,
            )
        return node

    def _or(self) -> dict[str, Any]:
        items = [self._and()]
        while self._is_word("or") or self._is_op("||"):
            self._advance()
            items.append(self._and())
        return items[0] if len(items) == 1 else {"any": items}

    def _and(self) -> dict[str, Any]:
        items = [self._not()]
        while self._is_word("and") or self._is_op("&&"):
            self._advance()
            items.append(self._not())
        return items[0] if len(items) == 1 else {"all": items}

    def _not(self) -> dict[str, Any]:
        if self._is_word("not") or self._is_op("!"):
            self._advance()
            return {"not": self._not()}
        return self._primary()

    def _primary(self) -> dict[str, Any]:
        if self._is_op("("):
            self._advance()
            node = self._or()
            if not self._is_op(")"):
                raise ConditionError("Missing ')'.", position=self.current.position)
            self._advance()
            return node
        return self._comparison()

    def _comparison(self) -> dict[str, Any]:
        token = self.current
        if token.kind != "word":
            raise ConditionError(
                f"Expected a fact name such as health.score, found {token.text or 'the end'!r}.",
                position=token.position,
            )
        fact = self._advance().text

        # Word operators first — "is set", "is not set", "not in", "contains"...
        if self._is_word("is"):
            self._advance()
            if self._is_word("set"):
                self._advance()
                return {"fact": fact, "op": "exists"}
            if self._is_word("missing"):
                self._advance()
                return {"fact": fact, "op": "not_exists"}
            if self._is_word("not"):
                self._advance()
                if self._is_word("set"):
                    self._advance()
                    return {"fact": fact, "op": "not_exists"}
                return {"fact": fact, "op": "ne", "value": self._value()}
            return {"fact": fact, "op": "eq", "value": self._value()}
        if self._is_word("exists"):
            self._advance()
            return {"fact": fact, "op": "exists"}
        if self._is_word("not"):
            position = self.current.position
            self._advance()
            if self._is_word("in"):
                self._advance()
                return {"fact": fact, "op": "not_in", "value": self._list()}
            if self._is_word("contains"):
                self._advance()
                return {"fact": fact, "op": "not_contains", "value": self._value()}
            if self._is_word("exists"):
                self._advance()
                return {"fact": fact, "op": "not_exists"}
            raise ConditionError("Expected 'in', 'contains' or 'exists' after 'not'.", position=position)
        if self._is_word("in"):
            self._advance()
            return {"fact": fact, "op": "in", "value": self._list()}
        if self._is_word("contains"):
            self._advance()
            return {"fact": fact, "op": "contains", "value": self._value()}
        if self._is_word("does"):
            # "does not contain" reads naturally and people will write it.
            self._advance()
            if self._is_word("not"):
                self._advance()
                if self._is_word("contain", "contains"):
                    self._advance()
                    return {"fact": fact, "op": "not_contains", "value": self._value()}
            raise ConditionError("Expected 'does not contain'.", position=self.current.position)
        if self._is_word("between"):
            self._advance()
            low = self._value()
            if not self._is_word("and"):
                raise ConditionError("Expected 'and' in 'between … and …'.", position=self.current.position)
            self._advance()
            high = self._value()
            return {"fact": fact, "op": "between", "value": [low, high]}
        if self.current.kind == "op" and self.current.text in OP_ALIASES:
            op = OP_ALIASES[self._advance().text]
            return {"fact": fact, "op": op, "value": self._value()}
        raise ConditionError(
            f"Expected an operator after {fact!r} (==, !=, <, <=, >, >=, in, contains, "
            f"between, is set), found {self.current.text or 'the end'!r}.",
            position=self.current.position,
        )

    def _value(self) -> Any:
        token = self.current
        if token.kind == "string":
            self._advance()
            return _unquote(token.text)
        if token.kind == "number":
            self._advance()
            return float(token.text) if "." in token.text else int(token.text)
        if token.kind == "op" and token.text == "[":
            return self._list()
        if token.kind == "word":
            lowered = token.lower
            if lowered in ("and", "or", "not"):
                raise ConditionError(f"Expected a value, found {token.text!r}.", position=token.position)
            self._advance()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            if lowered in ("null", "none"):
                return None
            # A bare word is a string: `health.band == at_risk` means what it looks like.
            return token.text
        raise ConditionError(f"Expected a value, found {token.text or 'the end'!r}.", position=token.position)

    def _list(self) -> list[Any]:
        if not self._is_op("["):
            raise ConditionError("Expected a list such as [\"a\", \"b\"].", position=self.current.position)
        self._advance()
        items: list[Any] = []
        if self._is_op("]"):
            self._advance()
            return items
        while True:
            items.append(self._value())
            if self._is_op(","):
                self._advance()
                continue
            if self._is_op("]"):
                self._advance()
                return items
            raise ConditionError("Expected ',' or ']' in the list.", position=self.current.position)


def parse(text: str) -> dict[str, Any]:
    """Parse the text form into the canonical JSON tree."""
    if len(text) > MAX_TEXT_LENGTH:
        raise ConditionError(f"A condition may be at most {MAX_TEXT_LENGTH} characters.")
    return _Parser(text).parse()


# ------------------------------------------------------------------- compile


@dataclass(slots=True, frozen=True)
class _Leaf:
    fact: str
    op: str
    value: Any
    spec: FactSpec

    def as_dict(self) -> dict[str, Any]:
        node: dict[str, Any] = {"fact": self.fact, "op": self.op}
        if self.op not in ("exists", "not_exists"):
            node["value"] = self.value
        return node


def _spec_for(fact: str) -> FactSpec:
    if fact in CATALOG:
        return CATALOG[fact]
    if fact.startswith(METADATA_PREFIX) and len(fact) > len(METADATA_PREFIX):
        return FactSpec(name=fact, type="any", description="Customer metadata you sent")
    if fact.startswith(REQUEST_PREFIX) and len(fact) > len(REQUEST_PREFIX):
        return FactSpec(name=fact, type="any", description="A value from the proposed action")
    suggestion = difflib.get_close_matches(fact, list(CATALOG), n=1, cutoff=0.6)
    hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
    raise ConditionError(
        f"Unknown fact {fact!r}.{hint} Custom values live under 'customer.metadata.'."
    )


def _check_enum(spec: FactSpec, value: Any) -> None:
    if not spec.values:
        return
    text = str(value)
    if text not in spec.values:
        suggestion = difflib.get_close_matches(text.lower().replace("-", "_"), spec.values, n=1, cutoff=0.5)
        hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
        raise ConditionError(
            f"{value!r} is not a value of {spec.name}.{hint} "
            f"Expected one of: {', '.join(spec.values)}."
        )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compile_leaf(node: dict[str, Any]) -> _Leaf:
    fact = node.get("fact")
    if not isinstance(fact, str) or not fact:
        raise ConditionError("A comparison needs a 'fact'.")
    spec = _spec_for(fact.strip())
    raw_op = str(node.get("op", "")).strip().lower()
    op = OP_ALIASES.get(raw_op, raw_op)
    if op not in OPERATORS:
        raise ConditionError(f"{node.get('op')!r} is not an operator.")
    allowed = OPS_FOR_TYPE[spec.type]
    if op not in allowed:
        readable = ", ".join(_TEXT_FOR_OP[name] for name in OPERATORS if name in allowed)
        raise ConditionError(
            f"{_TEXT_FOR_OP[op]!r} does not apply to {spec.name}, which is a {spec.type}. "
            f"Use one of: {readable}."
        )

    if op in ("exists", "not_exists"):
        return _Leaf(spec.name, op, None, spec)

    if "value" not in node:
        raise ConditionError(f"{spec.name} {_TEXT_FOR_OP[op]} needs a value.")
    value = node["value"]

    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise ConditionError(f"'{_TEXT_FOR_OP[op]}' needs a non-empty list.")
        if len(value) > MAX_LIST_VALUES:
            raise ConditionError(f"A list may hold at most {MAX_LIST_VALUES} values.")
        for item in value:
            if spec.type == "number" and not _is_number(item):
                raise ConditionError(f"{spec.name} is a number; {item!r} is not.")
            if spec.type == "enum":
                _check_enum(spec, item)
        return _Leaf(spec.name, op, list(value), spec)

    if op == "between":
        if not (isinstance(value, list) and len(value) == 2 and all(_is_number(item) for item in value)):
            raise ConditionError("'between' needs two numbers: between 7 and 30.")
        low, high = value
        if low > high:
            raise ConditionError(f"'between {low} and {high}' is empty — the lower bound comes first.")
        return _Leaf(spec.name, op, [low, high], spec)

    if isinstance(value, (list, dict)):
        raise ConditionError(f"'{_TEXT_FOR_OP[op]}' needs a single value, not a list.")

    if spec.type == "number":
        if not _is_number(value):
            raise ConditionError(f"{spec.name} is a number; {value!r} is not.")
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            raise ConditionError(f"{spec.name} is true or false; {value!r} is neither.")
    elif spec.type == "enum" or (spec.type == "list" and spec.values):
        # A list with a closed vocabulary (intents.kinds) is checked like an enum, so
        # `intents.kinds contains "cancel"` is caught as a typo for "cancellation".
        _check_enum(spec, value)
    elif spec.type in ("list", "terms", "string") and (
        value is None or (isinstance(value, str) and not value.strip())
    ):
        raise ConditionError(f"{spec.name} {_TEXT_FOR_OP[op]} needs a non-empty value.")
    return _Leaf(spec.name, op, value, spec)


def _compile(node: Any, depth: int, counter: list[int]) -> Any:
    if depth > MAX_DEPTH:
        raise ConditionError(f"A condition may nest at most {MAX_DEPTH} levels deep.")
    if not isinstance(node, dict):
        raise ConditionError("Each part of a condition must be an object.")
    keys = set(node)
    if keys & {"all", "any"}:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ConditionError(f"'{key}' needs a non-empty list.")
        compiled = [_compile(child, depth + 1, counter) for child in children]
        # Flatten all-in-all and any-in-any so the canonical form has one shape.
        flat: list[Any] = []
        for child in compiled:
            if isinstance(child, tuple) and child[0] == key:
                flat.extend(child[1])
            else:
                flat.append(child)
        return (key, flat)
    if "not" in node:
        return ("not", _compile(node["not"], depth + 1, counter))
    counter[0] += 1
    if counter[0] > MAX_LEAVES:
        raise ConditionError(f"A condition may hold at most {MAX_LEAVES} comparisons.")
    return _compile_leaf(node)


def _to_ast(node: Any) -> dict[str, Any]:
    if isinstance(node, _Leaf):
        return node.as_dict()
    kind, body = node
    if kind == "not":
        return {"not": _to_ast(body)}
    return {kind: [_to_ast(child) for child in body]}


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if _is_number(value):
        return str(int(value)) if float(value).is_integer() and isinstance(value, int) else str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value_text(item) for item in value) + "]"
    return json.dumps(str(value))


def _to_text(node: Any, parent: str | None = None) -> str:
    if isinstance(node, _Leaf):
        if node.op in ("exists", "not_exists"):
            return f"{node.fact} {_TEXT_FOR_OP[node.op]}"
        if node.op == "between":
            low, high = node.value
            return f"{node.fact} between {_value_text(low)} and {_value_text(high)}"
        return f"{node.fact} {_TEXT_FOR_OP[node.op]} {_value_text(node.value)}"
    kind, body = node
    if kind == "not":
        # Always bracketed: `not (plan == "enterprise")` cannot be misread as negating
        # only the fact name.
        return f"not ({_to_text(body, 'not')})"
    joiner = " and " if kind == "all" else " or "
    rendered = joiner.join(_to_text(child, kind) for child in body)
    # 'and' binds tighter than 'or', so only an 'any' inside an 'all' needs brackets —
    # and a composite under 'not' is bracketed by the caller above.
    if parent == "all" and kind == "any":
        return f"({rendered})"
    return rendered


# ------------------------------------------------------------------ evaluate

TRUE, FALSE, UNKNOWN = True, False, None


@dataclass(slots=True)
class LeafResult:
    """One comparison, as it was evaluated."""

    fact: str
    op: str
    expected: Any
    actual: Any
    outcome: bool | None
    note: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "true" if self.outcome is True else "false" if self.outcome is False else "unknown"

    def describe(self) -> str:
        expectation = (
            _TEXT_FOR_OP[self.op]
            if self.op in ("exists", "not_exists")
            else f"between {self.expected[0]} and {self.expected[1]}"
            if self.op == "between"
            else f"{_TEXT_FOR_OP[self.op]} {_value_text(self.expected)}"
        )
        if self.outcome is None:
            return f"{self.fact} {expectation}: unknown — {self.note or 'no value recorded'}"
        shown = _clip_value(self.actual)
        return f"{self.fact} {expectation}: {self.status} (is {shown})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "op": self.op,
            "expected": self.expected,
            "actual": _clip_value(self.actual, raw=True),
            "outcome": self.status,
            "note": self.note,
            "evidence": self.evidence,
            "description": self.describe(),
        }


def _clip_value(value: Any, *, raw: bool = False) -> Any:
    if isinstance(value, list):
        shown = value[:12]
        if raw:
            return shown + (["…"] if len(value) > 12 else [])
        return "[" + ", ".join(str(item) for item in shown) + (", …" if len(value) > 12 else "") + "]"
    if raw:
        return value
    return _value_text(value)


@dataclass(slots=True)
class Evaluation:
    """The result of evaluating a condition, with the leaves that decided it."""

    outcome: bool | None
    leaves: list[LeafResult]
    decisive: list[LeafResult]

    @property
    def matched(self) -> bool:
        """What a caller should act on: unknown counts as not matched."""
        return self.outcome is True

    @property
    def status(self) -> str:
        return "true" if self.outcome is True else "false" if self.outcome is False else "unknown"

    @property
    def evidence(self) -> list[str]:
        return list(dict.fromkeys(ident for leaf in self.decisive for ident in leaf.evidence))

    def explain(self) -> str:
        if not self.decisive:
            return f"{self.status}."
        return "; ".join(leaf.describe() for leaf in self.decisive)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.status,
            "matched": self.matched,
            "explanation": self.explain(),
            "evidence": self.evidence,
            "decisive": [leaf.as_dict() for leaf in self.decisive],
            "leaves": [leaf.as_dict() for leaf in self.leaves],
        }


def _number(value: Any) -> float | None:
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _same(actual: Any, expected: Any) -> bool:
    if _is_number(expected) or _is_number(actual):
        left, right = _number(actual), _number(expected)
        return left is not None and right is not None and left == right
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(actual) is bool(expected) and isinstance(actual, bool) == isinstance(expected, bool)
    return str(actual).strip().lower() == str(expected).strip().lower()


def _present(actual: Any) -> bool:
    if actual is None:
        return False
    if isinstance(actual, (list, dict, str)):
        return bool(actual)
    return True


def _evaluate_leaf(leaf: _Leaf, facts: CustomerFacts) -> LeafResult:
    actual = facts.get(leaf.fact)
    result = LeafResult(fact=leaf.fact, op=leaf.op, expected=leaf.value, actual=actual, outcome=None)
    op = leaf.op
    kind = leaf.spec.type

    if op == "exists":
        result.outcome = _present(actual)
    elif op == "not_exists":
        result.outcome = not _present(actual)
    elif actual is None:
        result.outcome = UNKNOWN
        result.note = "no value recorded"
    elif op in ("lt", "lte", "gt", "gte", "between") or (kind in ("number",) and op in ("eq", "ne")):
        number = _number(actual)
        if number is None:
            result.outcome = UNKNOWN
            result.note = f"{_clip_value(actual)} is not a number"
        elif op == "between":
            low, high = leaf.value
            result.outcome = low <= number <= high
        else:
            target = float(leaf.value)
            result.outcome = {
                "lt": number < target,
                "lte": number <= target,
                "gt": number > target,
                "gte": number >= target,
                "eq": number == target,
                "ne": number != target,
            }[op]
    elif op in ("eq", "ne"):
        same = _same(actual, leaf.value)
        result.outcome = same if op == "eq" else not same
    elif op in ("in", "not_in"):
        hit = any(_same(actual, item) for item in leaf.value)
        result.outcome = hit if op == "in" else not hit
    elif op in ("contains", "not_contains"):
        hit = _contains(kind, actual, leaf.value)
        result.outcome = hit if op == "contains" else not hit
    else:  # pragma: no cover - every operator is handled above
        result.outcome = UNKNOWN
        result.note = f"operator {op} not evaluable"

    # Evidence is what justifies the *actual* value — for a ``contains`` that held, the
    # memories that mention the thing; otherwise the memories behind the fact as a whole.
    value = leaf.value if op in ("contains", "not_contains") and result.outcome is not None else None
    result.evidence = facts.evidence_for(leaf.fact, value if op == "contains" and result.outcome else None)
    return result


def _contains(kind: str, actual: Any, expected: Any) -> bool:
    if kind == "terms":
        # Every word of the phrase, in any form of the word: `contains "billing issue"`
        # holds for a problem that says "issues with the bill". Compared on roots, which
        # fold derivation as well as inflection — see :func:`nlp.tokenize.root`.
        have = {root(str(item)) for item in (actual or [])}
        wanted = [root(word) for word in surface_words(str(expected))] or [
            root(word) for word in tokenize(str(expected))
        ]
        return bool(wanted) and all(word in have for word in wanted)
    if isinstance(actual, list):
        return any(_same(item, expected) for item in actual)
    if isinstance(actual, dict):
        return str(expected) in actual
    return str(expected).strip().lower() in str(actual).lower()


def _combine(kind: str, results: list[tuple[bool | None, list[LeafResult]]]) -> tuple[bool | None, list[LeafResult]]:
    outcomes = [outcome for outcome, _ in results]
    if kind == "all":
        if FALSE in outcomes:
            decisive = [leaf for outcome, leaves in results if outcome is FALSE for leaf in leaves]
            return FALSE, decisive
        if UNKNOWN in outcomes:
            decisive = [leaf for outcome, leaves in results if outcome is UNKNOWN for leaf in leaves]
            return UNKNOWN, decisive
        return TRUE, [leaf for _, leaves in results for leaf in leaves]
    # any
    if TRUE in outcomes:
        decisive = [leaf for outcome, leaves in results if outcome is TRUE for leaf in leaves]
        return TRUE, decisive
    if UNKNOWN in outcomes:
        decisive = [leaf for outcome, leaves in results if outcome is UNKNOWN for leaf in leaves]
        return UNKNOWN, decisive
    return FALSE, [leaf for _, leaves in results for leaf in leaves]


@dataclass(slots=True)
class Condition:
    """A validated condition, ready to evaluate as often as needed."""

    _tree: Any

    @property
    def ast(self) -> dict[str, Any]:
        return _to_ast(self._tree)

    @property
    def text(self) -> str:
        return _to_text(self._tree)

    @property
    def facts(self) -> list[str]:
        """Every fact the condition reads — used to explain what a rule depends on."""
        found: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, _Leaf):
                if node.fact not in found:
                    found.append(node.fact)
                return
            kind, body = node
            if kind == "not":
                walk(body)
            else:
                for child in body:
                    walk(child)

        walk(self._tree)
        return found

    def evaluate(self, facts: CustomerFacts) -> Evaluation:
        leaves: list[LeafResult] = []

        def run(node: Any) -> tuple[bool | None, list[LeafResult]]:
            if isinstance(node, _Leaf):
                result = _evaluate_leaf(node, facts)
                leaves.append(result)
                return result.outcome, [result]
            kind, body = node
            if kind == "not":
                outcome, decisive = run(body)
                # The same leaves decide the negated result — in the opposite direction.
                return (None if outcome is None else not outcome), decisive
            return _combine(kind, [run(child) for child in body])

        outcome, decisive = run(self._tree)
        return Evaluation(outcome=outcome, leaves=leaves, decisive=decisive)


def compile_condition(source: str | dict[str, Any]) -> Condition:
    """Validate a condition in either form. Raises :class:`ConditionError`."""
    if isinstance(source, str):
        stripped = source.strip()
        if stripped.startswith("{"):
            try:
                source = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ConditionError(f"Not valid JSON: {exc.msg}.", position=exc.pos) from exc
        else:
            source = parse(stripped)
    if not isinstance(source, dict):
        raise ConditionError("A condition must be text or a JSON object.")
    return Condition(_compile(source, 0, [0]))


def fact_catalog() -> list[dict[str, Any]]:
    """The catalog as data, with the operators each fact accepts."""
    return [
        {**spec.as_dict(), "operators": [_TEXT_FOR_OP[op] for op in OPERATORS if op in OPS_FOR_TYPE[spec.type]]}
        for spec in CATALOG.values()
    ]


# Fact families whose values are aggregates or the customer's own record, never a quote
# from a memory — shown to every reader (§17c).
_OPEN_FAMILIES = frozenset({"customer", "health", "signals", "state", "activity", "memories", "request"})


def is_content_fact(name: str) -> bool:
    """Whether a fact's value can carry words from a memory."""
    if name.split(".", 1)[0] in _OPEN_FAMILIES:
        return False
    spec = CATALOG.get(name)
    return spec is not None and spec.type in ("list", "terms", "string", "enum")


def sanitize_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    """A stored evaluation as a reader without clearance may see it.

    The rule (fact, operator, expected value) and the outcome stay: the rule is the
    project's own and the outcome is the decision. What goes is the *actual* value of any
    fact that can quote a memory — ``problems.entities is [shopify, hr]`` becomes
    ``[withheld]`` — because the evaluation was made on the full fact document and a trace
    is otherwise a side door around redaction.
    """

    def clean(leaf: dict[str, Any]) -> dict[str, Any]:
        if not is_content_fact(str(leaf.get("fact", ""))):
            return leaf
        safe = dict(leaf)
        safe["actual"] = WITHHELD
        status = safe.get("outcome", "unknown")
        expected = safe.get("expected")
        op = str(safe.get("op", ""))
        expectation = (
            _TEXT_FOR_OP.get(op, op)
            if op in ("exists", "not_exists")
            else f"{_TEXT_FOR_OP.get(op, op)} {_value_text(expected)}"
        )
        safe["description"] = f"{safe.get('fact')} {expectation}: {status} (value withheld)"
        return safe

    cleaned = dict(evaluation)
    cleaned["leaves"] = [clean(leaf) for leaf in evaluation.get("leaves", [])]
    cleaned["decisive"] = [clean(leaf) for leaf in evaluation.get("decisive", [])]
    if cleaned["decisive"]:
        cleaned["explanation"] = "; ".join(leaf["description"] for leaf in cleaned["decisive"])
    return cleaned
