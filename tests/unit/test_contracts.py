"""Memory contracts, as pure rules (§26 7.1)."""

from __future__ import annotations

import pytest

from memory_engine.contracts import (
    ContractError,
    compile_contract,
    infer,
    outcome,
    preview,
    validate,
    value_at,
)

PAYMENT = {
    "event": "payment_failed",
    "required": ["customer_id", "amount", "currency"],
    "fields": {
        "amount": {"type": "number", "minimum": 0},
        "currency": {"type": "string", "enum": ["USD", "EUR", "INR"]},
        "details.reason": {"type": "string", "max_length": 40},
        "card.last4": {"type": "string", "pattern": r"^\d{4}$"},
    },
    "text_field": "details.reason",
    "importance": 0.9,
}


def broken(data: dict, raw: dict | None = None) -> list[tuple[str, str]]:
    return [(item.path, item.rule) for item in validate(compile_contract(raw or PAYMENT), data)]


# ------------------------------------------------------------------ compile


def test_the_yaml_shape_compiles_to_one_canonical_form():
    contract = compile_contract(PAYMENT)
    assert (contract.event_type, contract.mode, contract.text_field, contract.importance) == (
        "payment_failed",
        "warn",
        "details.reason",
        0.9,
    )
    stored = contract.as_dict()
    assert stored["required"] == ["amount", "currency"], "customer_id belongs to the envelope"
    assert stored["fields"]["amount"] == {"type": "number", "required": True, "minimum": 0.0}
    assert stored["fields"]["card.last4"]["pattern"] == r"^\d{4}$"
    assert compile_contract(stored).as_dict() == stored, "the stored form compiles to itself"


def test_required_paths_need_no_field_spec_and_fields_may_be_a_list():
    contract = compile_contract({"event_type": "ticket_opened", "required": ["ticket.id"]})
    assert contract.field("ticket.id").required and contract.field("ticket.id").type == "any"
    listed = compile_contract(
        {"event_type": "ticket_opened", "fields": [{"path": "priority", "type": "string", "required": True}]}
    )
    assert listed.field("priority").required
    shorthand = compile_contract({"event_type": "ticket_opened", "fields": {"priority": "integer"}})
    assert shorthand.field("priority").type == "integer"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "A contract is an object"),
        ({"event": "Payment Failed!"}, "not a valid event type"),
        ({"event": "payment_failed", "mode": "strict"}, "'mode' is one of warn, enforce, off"),
        ({"event": "payment_failed", "fields": {"amount": {"type": "money"}}}, "type is one of"),
        ({"event": "payment_failed", "fields": {"amount": {"type": "number", "minimum": 5, "maximum": 1}}}, "greater than"),
        ({"event": "payment_failed", "fields": {"plan": {"type": "string", "minimum": 1}}}, "apply to numbers"),
        ({"event": "payment_failed", "fields": {"amount": {"minimum": "zero"}}}, "must be a number"),
        ({"event": "payment_failed", "fields": {"code": {"type": "string", "pattern": "(unclosed"}}}, "not a valid regular expression"),
        ({"event": "payment_failed", "fields": {"code": {"type": "string", "max_length": 0}}}, "at least 1"),
        ({"event": "payment_failed", "fields": {"code": {"type": "string", "enum": []}}}, "'enum' is a list"),
        ({"event": "payment_failed", "fields": {"bad path": {"type": "string"}}}, "not a valid field path"),
        ({"event": "payment_failed", "required": "amount"}, "'required' is a list"),
        ({"event": "payment_failed", "fields": {"amount": {"type": "number"}}, "text_field": "amount"}, "the text is a string"),
        ({"event": "payment_failed", "importance": 1.5}, "between 0 and 1"),
        ({"event": "payment_failed", "importance": True}, "must be a number"),
    ],
)
def test_a_bad_contract_is_refused_when_saved_with_a_reason(raw, message):
    with pytest.raises(ContractError, match=message):
        compile_contract(raw)


def test_a_contract_saved_under_one_type_cannot_name_another():
    with pytest.raises(ContractError, match="This contract is for 'payment_failed', not 'refund_issued'"):
        compile_contract({"event": "refund_issued"}, event_type="payment_failed")
    assert compile_contract({"mode": "enforce"}, event_type="payment_failed").event_type == "payment_failed"


# ------------------------------------------------------------------ validate


def test_every_way_a_payload_breaks_the_contract_is_reported():
    violations = validate(compile_contract(PAYMENT), {"amount": "₹500", "card": {"last4": "12"}})
    assert [(item.path, item.rule) for item in violations] == [
        ("amount", "type"),
        ("currency", "required"),
        ("card.last4", "pattern"),
    ]
    amount = violations[0]
    assert (amount.expected, amount.received) == ("number", 'string ("₹500")')
    assert amount.message == "'amount' should be a number; received string (\"₹500\")."


def test_missing_and_null_are_told_apart():
    found = {item.path: item.received for item in validate(compile_contract(PAYMENT), {"amount": None})}
    assert found == {"amount": "null", "currency": "missing"}


def test_bounds_enums_and_lengths():
    assert broken({"amount": -1, "currency": "GBP"}) == [("amount", "minimum"), ("currency", "enum")]
    long = validate(compile_contract(PAYMENT), {"amount": 1, "currency": "INR", "details": {"reason": "x" * 41}})
    assert [(item.rule, item.expected, item.received) for item in long] == [
        ("max_length", "at most 40 characters", "41 characters")
    ], "a long text is measured, never quoted"


@pytest.mark.parametrize(
    ("kind", "good", "bad"),
    [
        ("integer", [3, 3.0, -2], [3.5, True, "3"]),
        ("number", [3, 2.5], [True, "2.5", None]),
        ("boolean", [True, False], [0, "true"]),
        ("timestamp", ["2026-10-02T12:00:00Z", "2026-10-02"], ["yesterday", 1727870400]),
        ("email", ["priya@example.com"], ["priya at example", "@example.com"]),
        ("url", ["https://example.com/a?b=1"], ["example.com", "ftp://example.com"]),
        ("array", [[], [1, 2]], [{}, "a,b"]),
        ("object", [{}, {"a": 1}], [[], "{}"]),
        ("any", [1, "a", [], {}], []),
    ],
)
def test_types(kind, good, bad):
    contract = compile_contract({"event": "typed", "fields": {"value": {"type": kind}}})
    for value in good:
        assert validate(contract, {"value": value}) == [], (kind, value)
    for value in bad:
        rules = [item.rule for item in validate(contract, {"value": value})]
        assert rules == ([] if value is None else ["type"]), (kind, value)


def test_closed_contracts_flag_fields_they_do_not_name():
    raw = {**PAYMENT, "allow_extra": False}
    payload = {
        "amount": 1,
        "currency": "INR",
        "details": {"reason": "Declined.", "code": "05"},
        "importance": 0.5,
        "coupon": "SAVE10",
    }
    assert broken(payload, raw) == [("details.code", "unknown_field"), ("coupon", "unknown_field")], (
        "a declared field's parent and the platform's own importance are not extras"
    )
    assert broken(payload) == [], "open by default"
    nested = compile_contract({"event": "x", "allow_extra": False, "fields": {"meta": {"type": "object"}}})
    assert validate(nested, {"meta": {"anything": {"deep": 1}}}) == [], "inside a declared object is the object's"


def test_off_checks_nothing_and_an_empty_payload_is_not_an_error():
    contract = compile_contract({**PAYMENT, "mode": "off"})
    assert contract.checks is False
    assert broken({}, {"event": "x", "fields": {"note": {"type": "string"}}}) == []
    assert broken({}) == [("amount", "required"), ("currency", "required")]


def test_previews_are_short_and_redacted():
    assert preview("write to priya@example.com") == 'string ("write to [REDACTED_EMAIL]")'
    assert preview("a" * 60) == f'string ("{"a" * 39}…")'
    assert (preview(None), preview({"a": 1}), preview([1]), preview(4.5), preview(True)) == (
        "null",
        "object",
        "array",
        "number (4.5)",
        "boolean (True)",
    )
    assert value_at({"a": {"b": 2}}, "a.b") == 2 and value_at({"a": 1}, "a.b") is None


def test_the_outcome_stored_on_an_event():
    contract = compile_contract({**PAYMENT, "mode": "enforce"})
    stored = outcome(contract, validate(contract, {"amount": 5, "currency": "XYZ"}), version=3)
    assert (stored["version"], stored["mode"], stored["valid"]) == (3, "enforce", False)
    assert stored["violations"][0] == {
        "path": "currency",
        "rule": "enum",
        "expected": "one of USD, EUR, INR",
        "received": 'string ("XYZ")',
        "message": "'currency' should be one of USD, EUR, INR.",
    }


# ------------------------------------------------------------------ infer


SAMPLES = [
    {
        "amount": amount,
        "currency": currency,
        "email": "billing@acme.example",
        "failed_at": "2026-10-01T09:30:00Z",
        "details": {"reason": reason},
        **extra,
    }
    for amount, currency, reason, extra in [
        (499, "INR", "The card was declined by the issuing bank.", {}),
        (12.5, "USD", "Insufficient funds on the account this month.", {"importance": 0.9}),
        (99, "EUR", "The bank flagged the charge as suspicious.", {}),
        (499, "INR", "Card expired before the renewal date.", {"coupon": "SAVE10"}),
        (1500, "INR", "Three-D Secure was not completed in time.", {}),
    ]
]


def test_a_draft_is_learned_from_real_payloads():
    draft = infer("payment_failed", SAMPLES)
    assert draft["required"] == ["amount", "currency", "details", "details.reason", "email", "failed_at"]
    fields = draft["fields"]
    assert fields["amount"] == {"type": "number"}, "integers and decimals together are numbers"
    assert fields["currency"] == {"type": "string", "enum": ["EUR", "INR", "USD"]}
    assert (fields["email"]["type"], fields["failed_at"]["type"]) == ("email", "timestamp")
    assert fields["coupon"] == {"type": "string"}, "seen once: known, not required"
    assert "importance" not in fields, "the platform's own field"
    assert draft["text_field"] == "details.reason" and "enum" not in fields["details.reason"]
    assert (draft["mode"], draft["samples"]) == ("warn", 5)

    contract = compile_contract(draft)
    assert all(validate(contract, sample) == [] for sample in SAMPLES), "a draft accepts the traffic it came from"


def test_a_field_sent_as_different_types_is_shown_as_such():
    draft = infer("payment_failed", [{"amount": "₹500"}, {"amount": 499}, {"amount": 12.5}, {"amount": None}])
    assert draft["fields"]["amount"] == {"type": "any", "seen": {"string": 1, "integer": 1, "number": 1}}
    assert draft["required"] == ["amount"], "a null is still sent"
    assert compile_contract(draft).field("amount").type == "any", "the note is for reading; it compiles away"


def test_too_few_samples_make_no_enum_and_long_text_is_found_without_a_name():
    few = infer("plan_changed", [{"plan": "pro"}, {"plan": "team"}])
    assert few["fields"]["plan"] == {"type": "string"}
    unnamed = infer("call_logged", [{"summary_v2": "The customer asked about moving all invoices to a new GSTIN."}])
    assert unnamed["text_field"] == "summary_v2"
    assert infer("ping", [{"ok": True}])["text_field"] is None
