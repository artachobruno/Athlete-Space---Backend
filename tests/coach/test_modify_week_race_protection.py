"""Tests for MODIFY → week race and taper protection.

Tests that race week, race day, and taper weeks are protected from
invalid modifications.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.db.models import AthleteProfile, PlannedSession
from app.plans.modify.week_types import WeekModification
from app.plans.modify.week_validators import validate_week_modification
from app.plans.race.constants import TAPER_WEEKS_DEFAULT


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
        taper_weeks=TAPER_WEEKS_DEFAULT,
        sources={},
    )


@pytest.fixture
def race_week_sessions(race_date: date) -> list[PlannedSession]:
    """Fixture for sessions in race week."""
    week_start = race_date - timedelta(days=race_date.weekday())
    sessions = []
    for i in range(7):
        session_date = week_start + timedelta(days=i)
        session = PlannedSession(
            id=f"session_{i}",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title=f"Session {i}",
            plan_type="race",
            intent="easy" if i != 3 else "long",
        )
        sessions.append(session)
    return sessions


@pytest.fixture
def taper_week_sessions(race_date: date) -> list[PlannedSession]:
    """Fixture for sessions in taper week (2 weeks before race)."""
    week_start = race_date - timedelta(weeks=2, days=race_date.weekday())
    sessions = []
    for i in range(7):
        session_date = week_start + timedelta(days=i)
        session = PlannedSession(
            id=f"taper_session_{i}",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title=f"Taper Session {i}",
            plan_type="race",
            intent="easy" if i != 3 else "long",
        )
        sessions.append(session)
    return sessions


def test_increase_volume_during_race_week_blocked(
    athlete_profile: AthleteProfile,
    race_week_sessions: list[PlannedSession],
    race_date: date,
) -> None:
    """Test that increasing volume during race week is blocked."""
    week_start = race_date - timedelta(days=race_date.weekday())
    week_end = week_start + timedelta(days=6)

    modification = WeekModification(
        change_type="increase_volume",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        percent=0.1,
    )

    with pytest.raises(ValueError, match="Cannot increase volume during race week"):
        validate_week_modification(
            modification,
            race_week_sessions,
            athlete_profile=athlete_profile,
        )


def test_shift_days_touching_race_day_blocked(
    athlete_profile: AthleteProfile,
    race_week_sessions: list[PlannedSession],
    race_date: date,
) -> None:
    """Test that shifting race day is blocked unless explicitly allowed."""
    week_start = race_date - timedelta(days=race_date.weekday())
    week_end = week_start + timedelta(days=6)

    modification = WeekModification(
        change_type="shift_days",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        shift_map={race_date.isoformat(): (race_date + timedelta(days=1)).isoformat()},
        allow_race_day_shift=False,
    )

    with pytest.raises(ValueError, match="Race day cannot be shifted unless explicitly requested"):
        validate_week_modification(
            modification,
            race_week_sessions,
            athlete_profile=athlete_profile,
        )


def test_replace_day_in_taper_blocked(
    athlete_profile: AthleteProfile,
    taper_week_sessions: list[PlannedSession],
    race_date: date,
) -> None:
    """Test that replacing day in taper is blocked."""
    week_start = race_date - timedelta(weeks=2, days=race_date.weekday())
    week_end = week_start + timedelta(days=6)
    target_date = week_start + timedelta(days=2)

    modification = WeekModification(
        change_type="replace_day",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        target_date=target_date.isoformat(),
        day_modification={"change_type": "adjust_distance", "value": 5.0},
    )

    with pytest.raises(ValueError, match="Cannot add volume or quality sessions during taper"):
        validate_week_modification(
            modification,
            taper_week_sessions,
            athlete_profile=athlete_profile,
        )


def test_reduce_volume_in_taper_allowed(
    athlete_profile: AthleteProfile,
    taper_week_sessions: list[PlannedSession],
    race_date: date,
) -> None:
    """Test that reducing volume in taper is allowed."""
    week_start = race_date - timedelta(weeks=2, days=race_date.weekday())
    week_end = week_start + timedelta(days=6)

    modification = WeekModification(
        change_type="reduce_volume",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        percent=0.1,
    )

    # Should not raise
    validate_week_modification(
        modification,
        taper_week_sessions,
        athlete_profile=athlete_profile,
    )


def test_modification_without_race_date_allowed(
    race_week_sessions: list[PlannedSession],
    race_date: date,
) -> None:
    """Test that modifications are allowed when no race date is set."""
    week_start = race_date - timedelta(days=race_date.weekday())
    week_end = week_start + timedelta(days=6)

    modification = WeekModification(
        change_type="increase_volume",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        percent=0.1,
    )

    # Should not raise when athlete_profile is None
    validate_week_modification(
        modification,
        race_week_sessions,
        athlete_profile=None,
    )
