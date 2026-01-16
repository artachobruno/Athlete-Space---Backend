"""Race and taper utility functions.

Deterministic, stateless helpers for race/taper calculations.
Reusable across day/week/season validators.
"""

from datetime import date, timedelta

from app.plans.race.constants import TAPER_WEEKS_DEFAULT


def is_race_day(target_date: date, race_date: date | None) -> bool:
    """Check if target_date is the race day.

    Args:
        target_date: Date to check
        race_date: Race date or None

    Returns:
        True if target_date is the race day, False otherwise
    """
    return race_date is not None and target_date == race_date


def is_race_week(
    week_start: date,
    week_end: date,
    race_date: date | None,
) -> bool:
    """Check if the week range contains the race date.

    Args:
        week_start: Start date of the week
        week_end: End date of the week
        race_date: Race date or None

    Returns:
        True if the week contains the race date, False otherwise
    """
    if race_date is None:
        return False
    return week_start <= race_date <= week_end


def is_taper_week(
    week_start: date,
    race_date: date | None,
    taper_weeks: int,
) -> bool:
    """Check if the week is a taper week (within taper_weeks before race).

    Args:
        week_start: Start date of the week
        race_date: Race date or None
        taper_weeks: Number of taper weeks before race

    Returns:
        True if the week is a taper week, False otherwise
    """
    if race_date is None:
        return False
    delta_weeks = (race_date - week_start).days // 7
    return 0 < delta_weeks <= taper_weeks


def get_taper_start_date(race_date: date, taper_weeks: int) -> date:
    """Calculate the start date of the taper period.

    The taper period starts taper_weeks weeks before the race date.

    Args:
        race_date: Race date
        taper_weeks: Number of taper weeks before race

    Returns:
        Start date of the taper period
    """
    return race_date - timedelta(weeks=taper_weeks)
