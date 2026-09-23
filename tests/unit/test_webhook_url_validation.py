"""Webhook URL validation — the SSRF boundary."""

from __future__ import annotations

import pytest

from app.services.webhook_service import validate_event_types, validate_url
from common.errors import ValidationError
from common.settings import get_settings


def test_https_and_http_accepted_in_development():
    validate_url("https://example.com/hooks/memora")
    validate_url("http://localhost:9000/hook")  # how people develop against this


@pytest.mark.parametrize("url", ["ftp://example.com", "javascript:alert(1)", "example.com", ""])
def test_non_http_schemes_are_rejected(url):
    with pytest.raises(ValidationError):
        validate_url(url)


def test_url_length_is_bounded():
    with pytest.raises(ValidationError):
        validate_url("https://example.com/" + "a" * 2100)


def test_production_requires_https_and_public_addresses(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    try:
        with pytest.raises(ValidationError, match="https"):
            validate_url("http://example.com/hook")
        with pytest.raises(ValidationError, match="public address"):
            validate_url("https://localhost/hook")
        with pytest.raises(ValidationError, match="public address"):
            validate_url("https://127.0.0.1/hook")
    finally:
        monkeypatch.setattr(settings, "app_env", "test")


def test_event_type_validation():
    assert validate_event_types(["memory.created", "memory.created"]) == ["memory.created"]
    with pytest.raises(ValidationError):
        validate_event_types(["memory.exploded"])
