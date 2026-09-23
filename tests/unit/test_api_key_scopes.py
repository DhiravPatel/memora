"""API key scope semantics and validation."""

from __future__ import annotations

import pytest

from app.services.api_key_service import ApiKeyService
from common.enums import ApiKeyScope
from common.errors import ValidationError
from common.time import utcnow
from database.models import ApiKey


def key(scopes: list[str], **overrides) -> ApiKey:
    record = ApiKey(
        id="key_1",
        project_id="prj_1",
        name="Test",
        key_hash="hash",
        key_prefix="mk_test_abc",
        scopes=scopes,
    )
    for field, value in overrides.items():
        setattr(record, field, value)
    return record


def test_scope_check_is_exact():
    record = key([ApiKeyScope.EVENTS_WRITE.value])
    assert record.has_scope(ApiKeyScope.EVENTS_WRITE)
    assert not record.has_scope(ApiKeyScope.MEMORY_READ)


def test_admin_implies_every_ordinary_scope():
    record = key([ApiKeyScope.ADMIN.value])
    ordinary = set(ApiKeyScope) - ApiKeyScope.not_implied_by_admin()
    assert all(record.has_scope(scope) for scope in ordinary)


def test_admin_does_not_imply_clearance_to_read_restricted_memory():
    """The key a project is created with is an admin key, and it gets copied everywhere.

    If ``admin`` conferred clearance, the most widely-pasted credential in a deployment
    would read exactly the content a restriction policy exists to keep from it. Clearance
    is asked for, not inherited.
    """
    admin = key([ApiKeyScope.ADMIN.value])
    assert not admin.has_scope(ApiKeyScope.MEMORY_RESTRICTED)

    granted = key([ApiKeyScope.ADMIN.value, ApiKeyScope.MEMORY_RESTRICTED.value])
    assert granted.has_scope(ApiKeyScope.MEMORY_RESTRICTED)


def test_default_scopes_are_least_privilege():
    defaults = {scope.value for scope in ApiKeyScope.defaults()}
    assert ApiKeyScope.ADMIN.value not in defaults
    assert ApiKeyScope.MEMORY_WRITE.value not in defaults
    assert ApiKeyScope.EVENTS_WRITE.value in defaults


def test_revoked_and_expired_keys_are_inactive():
    from datetime import timedelta

    assert key([ApiKeyScope.ADMIN.value]).is_active
    assert not key([ApiKeyScope.ADMIN.value], revoked_at=utcnow()).is_active
    assert not key([ApiKeyScope.ADMIN.value], expires_at=utcnow() - timedelta(days=1)).is_active
    assert key([ApiKeyScope.ADMIN.value], expires_at=utcnow() + timedelta(days=1)).is_active


def test_scope_validation_rejects_unknown_values():
    with pytest.raises(ValidationError) as error:
        ApiKeyService._validate_scopes(["events:write", "everything"])
    assert "everything" in str(error.value)


def test_scope_validation_requires_at_least_one():
    with pytest.raises(ValidationError):
        ApiKeyService._validate_scopes([])


def test_scope_validation_deduplicates_and_keeps_order():
    assert ApiKeyService._validate_scopes(
        ["memory:read", "events:write", "memory:read"]
    ) == ["memory:read", "events:write"]
