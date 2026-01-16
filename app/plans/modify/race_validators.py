"""Validators for MODIFY → race operations.

Enforces invariants to prevent unsafe race modifications:
- Cannot move race earlier than today
- Cannot move race inside past weeks
- Race moved earlier → taper may overlap quality weeks (warning unless allowed)
- taper_weeks must be 1-6
- distance must be > 0
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.db.models import AthleteProfile, PlannedSession
from app.plans.modify.race_types import RaceModification
from app.plans.race.utils import get_taper_start_date


def start_of_week(d: date) -> date:
    """Calculate the start of the week (Monday) for a given date.

    Args:
        d: Date to get week start for

    Returns:
        Monday of the week containing the date
    """
    return d - timedelta(days=d.weekday())


def validate_race_date_not_in_past(new_race_date: date, today: date) -> None:
    """Validate race date is not in past weeks or earlier than today.

    Validation happens in this order:
    1. Check if date is in past weeks (week boundary)
    2. Check if date is earlier than today (day boundary)

    Args:
        new_race_date: New race date to validate
        today: Today's date

    Raises:
        ValueError: If race date is in past weeks or earlier than today
    """
    today_week_start = start_of_week(today)
    new_week_start = start_of_week(new_race_date)

    if new_week_start < today_week_start:
        raise ValueError("Cannot move race inside past weeks")

    if new_race_date < today:
        raise ValueError(
            f"Cannot move race earlier than today ({today}), got {new_race_date}"
        )


def validate_race_date_not_in_past_weeks(new_race_date: date, today: date) -> None:
    """Validate race date is not inside past weeks.

    Args:
        new_race_date: New race date to validate
        today: Today's date

    Raises:
        ValueError: If race date is in past weeks
    """
    # Check if race date is within the current week or past weeks
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)

    if new_race_date < week_start:
        raise ValueError(
            f"Cannot move race inside past weeks. "
            f"Current week starts {week_start}, got {new_race_date}"
        )


def validate_taper_weeks_range(taper_weeks: int) -> None:
    """Validate taper weeks is within valid range.

    Args:
        taper_weeks: Taper weeks to validate

    Raises:
        ValueError: If taper weeks is out of range
    """
    if taper_weeks < 1:
        raise ValueError(f"taper_weeks must be >= 1, got {taper_weeks}")
    if taper_weeks > 6:
        raise ValueError(f"taper_weeks must be <= 6, got {taper_weeks}")


def validate_distance_positive(distance_km: float) -> None:
    """Validate distance is positive.

    Args:
        distance_km: Distance to validate

    Raises:
        ValueError: If distance is not positive
    """
    if distance_km <= 0:
        raise ValueError(f"distance_km must be positive, got {distance_km}")


def check_taper_overlap_warning(
    race_date: date,
    old_race_date: date | None,
    taper_weeks: int,
    _athlete_profile: AthleteProfile | None,
) -> list[str]:
    """Check if moving race earlier causes taper to overlap quality weeks.

    This is a warning, not an error (unless allow_plan_inconsistency=False).

    Args:
        race_date: New race date
        old_race_date: Old race date (if known)
        taper_weeks: Taper length in weeks
        _athlete_profile: Optional athlete profile (unused, kept for future use)

    Returns:
        List of warning messages (empty if no warnings)
    """
    warnings: list[str] = []

    # Only warn if race is moved earlier
    if old_race_date is None or race_date >= old_race_date:
        return warnings

    # Calculate taper start date
    taper_start = get_taper_start_date(race_date, taper_weeks)

    # For now, we just log a warning - detailed quality week checking would require
    # access to planned sessions which is beyond the scope of this validator
    warnings.append(
        f"Race moved earlier from {old_race_date} to {race_date}. "
        f"Taper now starts {taper_start}. Review for quality week overlaps."
    )

    return warnings


def validate_race_modification(
    modification: RaceModification,
    athlete_profile: AthleteProfile | None,
    today: date | None = None,
) -> list[str]:
    """Validate race modification against athlete profile and constraints.

    This is the main validation entry point. It enforces all invariants.

    Args:
        modification: Race modification to validate
        athlete_profile: Optional athlete profile for current race state
        today: Today's date (defaults to current date)

    Returns:
        List of warning messages (empty if no warnings)

    Raises:
        ValueError: If validation fails
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    warnings: list[str] = []

    # Validate based on change_type
    if modification.change_type == "change_date":
        if modification.new_race_date is None:
            raise ValueError("change_date requires new_race_date")

        # Cannot move race earlier than today or inside past weeks
        # This checks both week boundaries and day boundaries
        validate_race_date_not_in_past(modification.new_race_date, today)

        # Check for taper overlap warnings
        old_race_date = athlete_profile.race_date if athlete_profile else None
        taper_weeks = (
            modification.new_taper_weeks
            if modification.new_taper_weeks is not None
            else (athlete_profile.taper_weeks if athlete_profile and athlete_profile.taper_weeks else 3)
        )

        overlap_warnings = check_taper_overlap_warning(
            modification.new_race_date,
            old_race_date,
            taper_weeks,
            athlete_profile,
        )
        warnings.extend(overlap_warnings)

        # Block if warnings exist and allow_plan_inconsistency is False
        if overlap_warnings and not modification.allow_plan_inconsistency:
            raise ValueError(
                f"Race moved earlier may cause plan inconsistency. "
                f"Set allow_plan_inconsistency=True to override. "
                f"Warnings: {', '.join(overlap_warnings)}"
            )

    elif modification.change_type == "change_taper":
        if modification.new_taper_weeks is None:
            raise ValueError("change_taper requires new_taper_weeks")

        validate_taper_weeks_range(modification.new_taper_weeks)

    elif modification.change_type == "change_distance":
        if modification.new_distance_km is None:
            raise ValueError("change_distance requires new_distance_km")

        validate_distance_positive(modification.new_distance_km)

    elif modification.change_type == "change_priority":
        if modification.new_priority is None:
            raise ValueError("change_priority requires new_priority")

        if modification.new_priority not in {"A", "B", "C"}:
            raise ValueError(
                f"Race priority must be one of: A, B, or C, got {modification.new_priority}"
            )

    logger.info(
        "Race modification validated",
        change_type=modification.change_type,
        warnings_count=len(warnings),
    )

    return warnings
