"""Project settings: the schema the form renders from and the writer enforces.

These used to be a free-form JSON blob. A typo like ``min_event_importance: 5`` silently
stopped every event from being extracted, and nothing anywhere would have told you.
"""

from __future__ import annotations

import pytest

from app.services.settings_service import (
    GROUPS,
    RANKING_KEYS,
    SCHEMA_BY_KEY,
    defaults,
    effective,
    schema,
    validate,
)
from common.errors import ValidationError
from database.models import Project


def project(settings: dict | None = None) -> Project:
    return Project(
        id="prj_1",
        organization_id="org_1",
        name="Test",
        api_key_hash="hash",
        api_key_prefix="mk_test_a",
        settings=settings or {},
    )


def test_every_field_is_documented_and_grouped():
    groups = {key for key, _ in GROUPS}
    for field in schema():
        assert field.help, f"{field.key} has no help text"
        assert field.group in groups, f"{field.key} is in an unknown group"
        assert field.default is not None


def test_defaults_match_the_deployment_settings():
    from common.settings import get_settings

    settings = get_settings()
    values = defaults()
    assert values["min_event_importance"] == settings.memory_min_event_importance
    assert values["consolidation_similarity"] == settings.memory_consolidation_similarity
    assert set(values["ranking_weights"]) == set(RANKING_KEYS)


def test_effective_layers_stored_over_defaults():
    values = effective(project({"min_event_importance": 0.6, "ranking_weights": {"recency": 0.4}}))
    assert values["min_event_importance"] == 0.6
    # A partial nested override keeps the other weights.
    assert values["ranking_weights"]["recency"] == 0.4
    assert values["ranking_weights"]["similarity"] == defaults()["ranking_weights"]["similarity"]
    assert values["decay_days"] == defaults()["decay_days"]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"min_event_importance": 5}, "at most 1"),
        ({"min_event_importance": -0.5}, "at least 0"),
        ({"consolidation_similarity": 0.99}, "at most 0.95"),
        ({"decay_days": -1}, "at least 0"),
        ({"context_token_budget": 10}, "at least 200"),
        ({"rate_limit_per_minute": 0}, "at least 1"),
        ({"pii_redaction_enabled": "yes"}, "true or false"),
        ({"ranking_weights": {"unknown": 1}}, "unknown keys"),
        ({"ranking_weights": "0.5"}, "must be an object"),
        ({"retention": {"forever": 10}}, "unknown keys"),
        ({"event_importance": {"page_view": 2}}, "at most 1"),
        ({"event_importance": {"": 0.5}}, "empty key"),
    ],
)
def test_out_of_range_values_are_refused(patch, message):
    with pytest.raises(ValidationError) as error:
        validate(patch)
    assert message in str(error.value)


def test_cross_field_rule_catches_a_meaningless_quota():
    with pytest.raises(ValidationError, match="meaningless"):
        validate({"monthly_event_quota": 100, "rate_limit_per_minute": 600})
    # The same quota is fine with a proportionate rate limit.
    assert validate({"monthly_event_quota": 100000, "rate_limit_per_minute": 600})


def test_whole_number_settings_stay_integers():
    """The stored JSON should read like the form, not like floating point noise."""
    cleaned = validate({"decay_days": 45.6, "rate_limit_per_minute": 600.0})
    assert cleaned["decay_days"] == 46
    assert isinstance(cleaned["decay_days"], int)
    assert isinstance(cleaned["rate_limit_per_minute"], int)


def test_percent_settings_keep_precision():
    assert validate({"min_event_importance": 0.3333333})["min_event_importance"] == 0.3333


def test_all_zero_ranking_weights_are_refused():
    with pytest.raises(ValidationError, match="cannot all be zero"):
        validate({"ranking_weights": dict.fromkeys(RANKING_KEYS, 0)})


def test_unknown_keys_pass_through_untouched():
    """A newer engine may understand settings this version does not."""
    assert validate({"future_setting": {"nested": True}}) == {"future_setting": {"nested": True}}


def test_event_importance_accepts_arbitrary_event_types():
    cleaned = validate({"event_importance": {"trial_extended": 0.9, "page_view": 0.02}})
    assert cleaned["event_importance"] == {"trial_extended": 0.9, "page_view": 0.02}


def test_schema_index_covers_every_field():
    assert set(SCHEMA_BY_KEY) == {field.key for field in schema()}


def test_numeric_steps_line_up_with_their_minimum():
    """A browser marks an input invalid unless (value - min) is a whole number of steps.

    With min=1 and step=10 the rate limit field reported its own default as invalid.
    """
    for field in schema():
        if field.step is None or field.minimum is None:
            continue
        offset = (field.default - field.minimum) / field.step if isinstance(field.default, (int, float)) else 0
        assert abs(offset - round(offset)) < 1e-9, f"{field.key} default is not a whole step above its minimum"
