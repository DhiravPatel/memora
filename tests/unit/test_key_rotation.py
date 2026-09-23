"""The rotation check's verdicts, and the header parsing they rest on."""

from __future__ import annotations

from datetime import timedelta

from common.crypto import derive_key, encrypt, key_id_of, needs_rewrite
from common.time import utcnow
from database.rotation import RotationStatus

KEY = derive_key("a" * 48)
OTHER = derive_key("b" * 48)


def status(**overrides) -> RotationStatus:
    fields = {
        "key_id": KEY.id,
        "first_seen_at": utcnow() - timedelta(days=10),
        "age_days": 10,
        "max_age_days": 90,
        "stale_secrets": 0,
    }
    fields.update(overrides)
    return RotationStatus(**fields)


# ------------------------------------------------------------------ header reading


def test_the_key_that_sealed_a_value_is_readable_without_the_key():
    """The whole check depends on this: it must work for keys this process cannot use."""
    sealed = encrypt("s3cret", KEY)
    assert key_id_of(sealed) == KEY.id
    assert key_id_of("not encrypted") is None
    assert key_id_of(None) is None
    assert key_id_of("enc:v1:truncated") is None


def test_plaintext_and_retired_keys_both_need_a_rewrite():
    assert needs_rewrite("plaintext", KEY.id)
    assert needs_rewrite(encrypt("s", OTHER), KEY.id)
    assert not needs_rewrite(encrypt("s", KEY), KEY.id)
    # An empty column is not a secret that failed to rotate.
    assert not needs_rewrite("", KEY.id)
    assert not needs_rewrite(None, KEY.id)


# ------------------------------------------------------------------------ verdicts


def test_a_young_key_with_nothing_outstanding_is_quiet():
    assert not status().needs_attention


def test_a_key_past_the_interval_is_overdue():
    assert status(age_days=120).overdue
    assert status(age_days=120).needs_attention


def test_the_interval_is_inclusive_at_the_boundary():
    """Exactly at the limit is not yet past it, or every check fires a day early."""
    assert not status(age_days=90).overdue
    assert status(age_days=91).overdue


def test_zero_disables_the_age_check():
    assert not status(age_days=4000, max_age_days=0).overdue


def test_an_unfinished_rotation_is_reported_even_on_a_new_key():
    """The dangerous state: new key set, rewrite never run, old key about to be retired."""
    unfinished = status(age_days=1, stale_secrets=3)
    assert unfinished.unfinished
    assert unfinished.needs_attention
    assert not unfinished.overdue


def test_the_report_is_serialisable_for_a_log_line():
    assert status(age_days=120, stale_secrets=2).as_dict() == {
        "key_id": KEY.id,
        "age_days": 120,
        "max_age_days": 90,
        "stale_secrets": 2,
        "overdue": True,
        "unfinished": True,
    }
