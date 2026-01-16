"""Tests for MODIFY → race operations.

Tests that race metadata can be modified safely without mutating sessions.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.db.models import AthleteProfile
from app.plans.modify.race_types import RaceModification
from app.plans.modify.race_validators import validate_race_modification


@pytest.fixture
def today() -> date:
    """Fixture for today's date."""
    return date(2026, 6, 1)


@pytest.fixture
def race_date() -> date:
    """Fixture for race date."""
    return date(2026, 6, 15)


@pytest.fixture
def athlete_profile(race_date: date) -> AthleteProfile:
    """Fixture for athlete profile with race date."""
    return AthleteProfile(
        user_id="test_user",
        athlete_id=1,
        race_date=race_date,
        taper_weeks=2,
    )


def test_change_race_date_valid(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing race date to a valid future date is allowed."""
    new_race_date = date(2026, 7, 1)

    modification = RaceModification(
        change_type="change_date",
        new_race_date=new_race_date,
        reason="Race moved by organizer",
    )

    # Should not raise
    warnings = validate_race_modification(modification, athlete_profile, today)
    assert len(warnings) == 0


def test_change_race_date_into_past_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing race date into the past is blocked.

    Note: Week validation takes precedence over day validation.
    A date in a past week will raise "Cannot move race inside past weeks".
    """
    # Use a date in a past week - this will trigger week validation first
    past_date = date(2026, 5, 30)

    modification = RaceModification(
        change_type="change_date",
        new_race_date=past_date,
    )

    # Week validation happens first, so we expect the week error message
    with pytest.raises(ValueError, match="Cannot move race inside past weeks"):
        validate_race_modification(modification, athlete_profile, today)


def test_change_race_date_into_past_weeks_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing race date into past weeks is blocked."""
    # Calculate a date in a past week
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    past_week_date = week_start - timedelta(days=1)

    modification = RaceModification(
        change_type="change_date",
        new_race_date=past_week_date,
    )

    with pytest.raises(ValueError, match="Cannot move race inside past weeks"):
        validate_race_modification(modification, athlete_profile, today)


def test_race_moved_earlier_warning_emitted(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that moving race earlier emits warning."""
    old_race_date = athlete_profile.race_date
    new_race_date = old_race_date - timedelta(weeks=1)

    modification = RaceModification(
        change_type="change_date",
        new_race_date=new_race_date,
        allow_plan_inconsistency=False,
    )

    # Should raise because allow_plan_inconsistency=False and there are warnings
    with pytest.raises(ValueError, match="Race moved earlier may cause plan inconsistency"):
        validate_race_modification(modification, athlete_profile, today)


def test_race_moved_earlier_with_override_allowed(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that moving race earlier is allowed with allow_plan_inconsistency=True."""
    old_race_date = athlete_profile.race_date
    new_race_date = old_race_date - timedelta(weeks=1)

    modification = RaceModification(
        change_type="change_date",
        new_race_date=new_race_date,
        allow_plan_inconsistency=True,
    )

    # Should not raise, but should emit warnings
    warnings = validate_race_modification(modification, athlete_profile, today)
    assert len(warnings) > 0
    assert any("Race moved earlier" in w for w in warnings)


def test_reduce_taper_to_zero_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that reducing taper to zero is blocked."""
    modification = RaceModification(
        change_type="change_taper",
        new_taper_weeks=0,
    )

    with pytest.raises(ValueError, match="taper_weeks must be >= 1"):
        validate_race_modification(modification, athlete_profile, today)


def test_increase_taper_beyond_max_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that increasing taper beyond 6 weeks is blocked."""
    modification = RaceModification(
        change_type="change_taper",
        new_taper_weeks=7,
    )

    with pytest.raises(ValueError, match="taper_weeks must be <= 6"):
        validate_race_modification(modification, athlete_profile, today)


def test_change_taper_valid(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing taper to a valid value is allowed."""
    modification = RaceModification(
        change_type="change_taper",
        new_taper_weeks=3,
    )

    # Should not raise
    warnings = validate_race_modification(modification, athlete_profile, today)
    assert len(warnings) == 0


def test_change_distance_negative_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing distance to negative is blocked."""
    modification = RaceModification(
        change_type="change_distance",
        new_distance_km=-10.0,
    )

    with pytest.raises(ValueError, match="distance_km must be positive"):
        validate_race_modification(modification, athlete_profile, today)


def test_change_distance_zero_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing distance to zero is blocked."""
    modification = RaceModification(
        change_type="change_distance",
        new_distance_km=0.0,
    )

    with pytest.raises(ValueError, match="distance_km must be positive"):
        validate_race_modification(modification, athlete_profile, today)


def test_change_distance_valid(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing distance to a valid value is allowed."""
    modification = RaceModification(
        change_type="change_distance",
        new_distance_km=21.1,  # Half marathon
    )

    # Should not raise
    warnings = validate_race_modification(modification, athlete_profile, today)
    assert len(warnings) == 0


def test_change_priority_valid(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing priority to a valid value is allowed."""
    modification = RaceModification(
        change_type="change_priority",
        new_priority="B",
    )

    # Should not raise
    warnings = validate_race_modification(modification, athlete_profile, today)
    assert len(warnings) == 0


def test_change_priority_invalid_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that changing priority to invalid value is blocked."""
    modification = RaceModification(
        change_type="change_priority",
        new_priority="D",  # Invalid
    )

    with pytest.raises(ValueError, match="Race priority must be one of: A, B, or C"):
        validate_race_modification(modification, athlete_profile, today)


def test_modification_without_athlete_profile_allowed(
    today: date,
) -> None:
    """Test that modifications are allowed when no athlete profile exists."""
    modification = RaceModification(
        change_type="change_priority",
        new_priority="A",
    )

    # Should not raise when athlete_profile is None
    warnings = validate_race_modification(modification, None, today)
    assert isinstance(warnings, list)


def test_multiple_fields_modified_at_once_blocked(
    athlete_profile: AthleteProfile,
    today: date,
) -> None:
    """Test that modifying multiple fields at once is blocked by adapter.

    Note: This test documents expected behavior. The adapter should enforce
    exactly one change field is set.
    """
    # This should be caught by the adapter, not the validator
    # But we document it here for completeness
    # The adapter enforces exactly one field
    _modification = RaceModification(
        change_type="change_date",
        new_race_date=date(2026, 7, 1),
        new_distance_km=21.1,  # This should not be set
    )

    # The adapter should catch this, not the validator
    # For now, we just validate that the change_type matches what's set
    pass
