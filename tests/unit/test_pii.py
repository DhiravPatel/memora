"""PII detection and redaction."""

from __future__ import annotations

from common.pii import contains_pii, detect, redact_payload, redact_text


def test_detects_common_pii_kinds():
    kinds = {finding.kind for finding in detect("john@example.com +1 415 555 2671 4111 1111 1111 1111")}
    assert {"email", "phone", "card"} <= kinds


def test_redacts_text():
    redacted = redact_text("Reach me at john@example.com or 415-555-2671")
    assert "john@example.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted


def test_redacts_secrets_before_anything_else():
    assert "sk_live_" not in redact_text("key sk_live_abc123456789")


def test_sensitive_keys_are_removed_wholesale():
    payload = redact_payload({"password": "hunter2", "nested": {"access_token": "abc", "ok": "value"}})
    assert payload["password"] == "[REDACTED]"
    assert payload["nested"]["access_token"] == "[REDACTED]"
    assert payload["nested"]["ok"] == "value"


def test_clean_text_is_left_alone():
    text = "Customer cannot connect Shopify."
    assert redact_text(text) == text
    assert contains_pii(text) is False
