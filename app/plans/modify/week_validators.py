"""Validators for MODIFY → week operations.

Enforces invariants to prevent silent corruption:
- Range length <= 7 days
- Sessions exist in range
- Exactly one long run in range
- No duplicate days after shift
- Volume bounds (0 < percent <= 0.6)
- Race week/taper protection
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.db.models import AthleteProfile, PlannedSession
from app.plans.modify.week_types import WeekModification
from app.plans.race.constants import TAPER_WEEKS_DEFAULT
from app.plans.race.utils import is_race_week, is_taper_week


def validate_week_range(start_date: date, end_date: date) -> None:
    """Validate week range is valid (<= 7 days).

    Args:
        start_date: Start date of range
        end_date: End date of range

    Raises:
        ValueError: If range is invalid or too long
    """
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must be <= end_date ({end_date})")

    days_diff = (end_date - start_date).days + 1
    if days_diff > 7:
        raise ValueError(f"Week range must be <= 7 days, got {days_diff} days")


def validate_sessions_exist(sessions: list[PlannedSession], start_date: date, end_date: date) -> None:
    """Validate sessions exist in range (warning, not error).

    Args:
        sessions: Sessions in range
        start_date: Start date of range
        end_date: End date of range
    """
    if not sessions:
        logger.warning(f"No sessions found in range {start_date} to {end_date}")


def validate_one_long_run(sessions: list[PlannedSession]) -> None:
    """Validate exactly one long run in range.

    Args:
        sessions: Sessions in range

    Raises:
        ValueError: If more than one long run found
    """
    long_runs = [s for s in sessions if s.intent == "long"]
    if len(long_runs) > 1:
        raise ValueError(f"Expected at most one long run, found {len(long_runs)}")


def validate_volume_percent(percent: float) -> None:
    """Validate volume percent is within bounds.

    Args:
        percent: Percentage change (0.2 = 20%)

    Raises:
        ValueError: If percent is out of bounds
    """
    if percent <= 0:
        raise ValueError(f"percent must be positive, got {percent}")
    if percent > 0.6:
        raise ValueError(f"percent must be <= 0.6 (60%), got {percent}")


def validate_shift_no_collisions(shift_map: dict[str, str], existing_sessions: list[PlannedSession]) -> None:
    """Validate shift map doesn't create collisions.

    Args:
        shift_map: Mapping of old dates to new dates
        existing_sessions: Existing sessions in range

    Raises:
        ValueError: If shift would create collision
    """
    # Get all target dates from shift_map
    target_dates = set(shift_map.values())

    # Check for duplicates in shift_map itself
    if len(target_dates) < len(shift_map):
        raise ValueError("shift_map creates duplicate target dates")

    # Parse target dates
    try:
        target_dates_parsed = {date.fromisoformat(d) for d in target_dates}
    except ValueError as e:
        raise ValueError(f"Invalid date format in shift_map: {e}") from e

    # Check for collisions with existing sessions on target dates
    existing_dates = {s.date.date() for s in existing_sessions}

    collisions = target_dates_parsed.intersection(existing_dates)
    if collisions:
        collision_str = ", ".join(str(d) for d in sorted(collisions))
        raise ValueError(f"shift_map creates collisions on dates: {collision_str}")


def validate_week_modification(
    modification: WeekModification,
    sessions: list[PlannedSession],
    *,
    athlete_profile: AthleteProfile | None = None,
) -> None:
    """Validate week modification against sessions.

    This is the main validation entry point. It enforces all invariants.

    Args:
        modification: Week modification to validate
        sessions: Sessions in the modification range
        athlete_profile: Optional athlete profile for race/taper protection

    Raises:
        ValueError: If validation fails
    """
    # Parse dates
    try:
        start_date = date.fromisoformat(modification.start_date)
        end_date = date.fromisoformat(modification.end_date)
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}") from e

    # Validate range length
    validate_week_range(start_date, end_date)

    # Validate sessions exist (warning only)
    validate_sessions_exist(sessions, start_date, end_date)

    # Fetch athlete race context for protection
    race_date: date | None = None
    taper_weeks: int = TAPER_WEEKS_DEFAULT
    if athlete_profile:
        if athlete_profile.race_date:
            race_date = athlete_profile.race_date
        if athlete_profile.taper_weeks is not None:
            taper_weeks = athlete_profile.taper_weeks

    # Race and taper protection
    if race_date:
        # Rule: No volume increase in race week
        if is_race_week(start_date, end_date, race_date) and modification.change_type == "increase_volume":
            logger.warning(
                "modify_blocked_by_race_rules",
                change_type=modification.change_type,
                range_start=start_date,
                range_end=end_date,
                race_date=race_date,
            )
            raise ValueError("Cannot increase volume during race week.")

        # Rule: No shifting race day (unless explicit override)
        if modification.change_type == "shift_days" and modification.shift_map:
            race_date_str = race_date.isoformat()
            if race_date_str in modification.shift_map and not modification.allow_race_day_shift:
                logger.warning(
                    "modify_blocked_by_race_rules",
                    change_type=modification.change_type,
                    range_start=start_date,
                    range_end=end_date,
                    race_date=race_date,
                )
                raise ValueError("Race day cannot be shifted unless explicitly requested.")

        # Rule: Only reductions allowed in taper
        if (
            is_taper_week(start_date, race_date, taper_weeks)
            and modification.change_type not in {"reduce_volume"}
        ):
            logger.warning(
                "modify_blocked_by_race_rules",
                change_type=modification.change_type,
                range_start=start_date,
                range_end=end_date,
                race_date=race_date,
            )
            raise ValueError("Cannot add volume or quality sessions during taper.")

    # Validate change_type-specific invariants
    if modification.change_type in {"reduce_volume", "increase_volume"}:
        # Validate percent if provided
        if modification.percent is not None:
            validate_volume_percent(modification.percent)

        # Must have exactly one of percent or miles
        if modification.percent is None and modification.miles is None:
            raise ValueError(f"{modification.change_type} requires either percent or miles")

        # Validate one long run constraint
        validate_one_long_run(sessions)

    elif modification.change_type == "shift_days":
        if not modification.shift_map:
            raise ValueError("shift_days requires shift_map")

        # Validate no collisions
        validate_shift_no_collisions(modification.shift_map, sessions)

    elif modification.change_type == "replace_day":
        if not modification.target_date:
            raise ValueError("replace_day requires target_date")
        if not modification.day_modification:
            raise ValueError("replace_day requires day_modification")

        # Validate target_date is in range
        try:
            target_date_parsed = date.fromisoformat(modification.target_date)
        except ValueError as e:
            raise ValueError(f"Invalid target_date format: {e}") from e

        if target_date_parsed < start_date or target_date_parsed > end_date:
            raise ValueError(
                f"target_date ({modification.target_date}) must be within range "
                f"[{modification.start_date}, {modification.end_date}]"
            )

    logger.info(
        "Week modification validated",
        change_type=modification.change_type,
        session_count=len(sessions),
    )
