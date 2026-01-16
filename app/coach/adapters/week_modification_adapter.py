"""Adapter to convert extracted week modification to structured WeekModification.

This layer bridges LLM extraction and execution.
It resolves relative dates, enforces invariants, and validates.
NO LLM calls here.
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.coach.extraction.modify_week_extractor import ExtractedWeekModification
from app.plans.modify.week_types import WeekModification


def resolve_relative_date(date_str: str, today: date) -> date:
    """Resolve relative date strings to concrete dates.

    Args:
        date_str: Date string (YYYY-MM-DD or relative like "this week", "next week")
        today: Today's date for relative resolution

    Returns:
        Concrete date

    Raises:
        ValueError: If date cannot be resolved
    """
    if date_str is None:
        raise ValueError("Date string is None")

    date_lower = date_str.lower().strip()

    # Try parsing as YYYY-MM-DD first
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        pass

    # Resolve relative dates
    if date_lower == "this week":
        # Start of this week (Monday)
        days_since_monday = today.weekday()
        return today - timedelta(days=days_since_monday)

    if date_lower == "next week":
        # Start of next week (Monday)
        days_since_monday = today.weekday()
        days_until_next_monday = 7 - days_since_monday
        return today + timedelta(days=days_until_next_monday)

    if date_lower.startswith("in ") and date_lower.endswith(" weeks"):
        # e.g., "in 2 weeks"
        try:
            weeks = int(date_lower.split()[1])
            return today + timedelta(weeks=weeks)
        except (ValueError, IndexError):
            pass

    # If we can't resolve it, raise error
    raise ValueError(f"Cannot resolve relative date: {date_str}")


def to_week_modification(
    extracted: ExtractedWeekModification,
    today: date | None = None,
) -> WeekModification:
    """Convert extracted attributes to structured WeekModification.

    This function:
    - Resolves relative dates to concrete dates
    - Enforces invariants (percent XOR miles for volume changes)
    - Validates required fields per change_type
    - Returns validated WeekModification

    Args:
        extracted: Extracted attributes from LLM
        today: Today's date for relative date resolution

    Returns:
        WeekModification ready for execution

    Raises:
        ValueError: If validation fails or required fields missing
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    if extracted.change_type is None:
        raise ValueError("change_type is required but was not extracted")

    # Resolve dates
    start_date: date | None = None
    end_date: date | None = None

    if extracted.start_date:
        try:
            start_date = resolve_relative_date(extracted.start_date, today)
        except ValueError as e:
            raise ValueError(f"Cannot resolve start_date: {e}") from e

    if extracted.end_date:
        try:
            end_date = resolve_relative_date(extracted.end_date, today)
        except ValueError as e:
            raise ValueError(f"Cannot resolve end_date: {e}") from e

    # Default to this week if no dates provided
    if start_date is None and end_date is None:
        days_since_monday = today.weekday()
        start_date = today - timedelta(days=days_since_monday)
        end_date = start_date + timedelta(days=6)

    # Ensure we have both dates
    if start_date is None or end_date is None:
        raise ValueError("Both start_date and end_date are required or must be defaulted")

    # Validate change_type-specific requirements
    if extracted.change_type in {"reduce_volume", "increase_volume"}:
        # Must have exactly one of percent or miles
        if extracted.percent is None and extracted.miles is None:
            raise ValueError(f"{extracted.change_type} requires either percent or miles")
        if extracted.percent is not None and extracted.miles is not None:
            raise ValueError(f"{extracted.change_type} requires exactly one of percent or miles, not both")

        # Validate percent is positive
        if extracted.percent is not None and extracted.percent <= 0:
            raise ValueError(f"percent must be positive, got {extracted.percent}")

        # For reduce_volume, miles should be negative or percent should be positive
        if extracted.change_type == "reduce_volume" and extracted.miles is not None and extracted.miles > 0:
            logger.warning("reduce_volume with positive miles - treating as negative")
            extracted.miles = -extracted.miles

        # For increase_volume, miles should be positive
        if extracted.change_type == "increase_volume" and extracted.miles is not None and extracted.miles < 0:
            logger.warning("increase_volume with negative miles - treating as positive")
            extracted.miles = abs(extracted.miles)

    elif extracted.change_type == "shift_days":
        if not extracted.shift_map:
            raise ValueError("shift_days requires shift_map")

        # Validate shift_map dates are valid
        for old_date_str, new_date_str in extracted.shift_map.items():
            try:
                resolve_relative_date(old_date_str, today)
                resolve_relative_date(new_date_str, today)
            except ValueError as e:
                raise ValueError(f"Invalid date in shift_map: {e}") from e

    elif extracted.change_type == "replace_day":
        if not extracted.target_date:
            raise ValueError("replace_day requires target_date")
        if not extracted.day_modification:
            raise ValueError("replace_day requires day_modification")

        # Validate target_date
        try:
            resolve_relative_date(extracted.target_date, today)
        except ValueError as e:
            raise ValueError(f"Cannot resolve target_date: {e}") from e

    # Build WeekModification
    week_mod = WeekModification(
        change_type=extracted.change_type,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        reason=extracted.reason,
        percent=extracted.percent,
        miles=extracted.miles,
        shift_map=extracted.shift_map,
        target_date=extracted.target_date,
        day_modification=extracted.day_modification,
    )

    logger.info(
        "Converted extracted attributes to WeekModification",
        change_type=week_mod.change_type,
        start_date=week_mod.start_date,
        end_date=week_mod.end_date,
    )

    return week_mod
