"""Adapter to convert extracted race modification to structured RaceModification.

This layer bridges LLM extraction and execution.
It resolves relative dates, enforces invariants, and validates.
NO LLM calls here.
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.coach.extraction.modify_race_extractor import ExtractedRaceModification
from app.plans.modify.race_types import RaceModification


def resolve_relative_date(date_str: str, today: date) -> date:
    """Resolve relative date strings to concrete dates.

    Args:
        date_str: Date string (YYYY-MM-DD or relative like "next month", "two weeks later")
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
    if date_lower == "next month":
        # First day of next month
        if today.month == 12:
            return date(today.year + 1, 1, 1)
        return date(today.year, today.month + 1, 1)

    if date_lower.startswith("in ") and date_lower.endswith(" weeks"):
        # e.g., "in 2 weeks"
        try:
            weeks = int(date_lower.split()[1])
            return today + timedelta(weeks=weeks)
        except (ValueError, IndexError):
            pass

    if "two weeks later" in date_lower or "2 weeks later" in date_lower:
        return today + timedelta(weeks=2)

    if "one week later" in date_lower or "1 week later" in date_lower:
        return today + timedelta(weeks=1)

    # If we can't resolve it, raise error
    raise ValueError(f"Cannot resolve relative date: {date_str}")


def to_race_modification(
    extracted: ExtractedRaceModification,
    today: date | None = None,
) -> RaceModification:
    """Convert extracted attributes to structured RaceModification.

    This function:
    - Resolves relative dates to concrete dates
    - Enforces invariants (exactly one change per request)
    - Validates required fields per change_type
    - Clamps taper weeks (1-6)
    - Validates distance > 0
    - Returns validated RaceModification

    Args:
        extracted: Extracted attributes from LLM
        today: Today's date for relative date resolution

    Returns:
        RaceModification ready for execution

    Raises:
        ValueError: If validation fails or required fields missing
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    if extracted.change_type is None:
        raise ValueError("change_type is required but was not extracted")

    # Resolve new_race_date if provided
    new_race_date: date | None = None
    if extracted.new_race_date:
        try:
            new_race_date = resolve_relative_date(extracted.new_race_date, today)
        except ValueError as e:
            raise ValueError(f"Cannot resolve new_race_date: {e}") from e

    # Validate change_type-specific requirements
    if extracted.change_type == "change_date":
        if new_race_date is None:
            raise ValueError("change_date requires new_race_date")
    elif extracted.change_type == "change_distance":
        if extracted.new_distance_km is None:
            raise ValueError("change_distance requires new_distance_km")
        if extracted.new_distance_km <= 0:
            raise ValueError(f"new_distance_km must be positive, got {extracted.new_distance_km}")
    elif extracted.change_type == "change_priority":
        if extracted.new_priority is None:
            raise ValueError("change_priority requires new_priority")
        if extracted.new_priority not in {"A", "B", "C"}:
            raise ValueError(f"new_priority must be A, B, or C, got {extracted.new_priority}")
    elif extracted.change_type == "change_taper":
        if extracted.new_taper_weeks is None:
            raise ValueError("change_taper requires new_taper_weeks")
        # Clamp taper weeks to 1-6
        if extracted.new_taper_weeks < 1:
            raise ValueError(f"new_taper_weeks must be >= 1, got {extracted.new_taper_weeks}")
        if extracted.new_taper_weeks > 6:
            logger.warning(f"new_taper_weeks > 6, clamping to 6: {extracted.new_taper_weeks}")
            extracted.new_taper_weeks = 6

    # Ensure exactly one change field is set
    change_fields = [
        new_race_date is not None,
        extracted.new_distance_km is not None,
        extracted.new_priority is not None,
        extracted.new_taper_weeks is not None,
    ]
    if sum(change_fields) != 1:
        raise ValueError(
            f"Exactly one change field must be set for {extracted.change_type}, "
            f"got: date={new_race_date}, distance={extracted.new_distance_km}, "
            f"priority={extracted.new_priority}, taper={extracted.new_taper_weeks}"
        )

    # Build RaceModification
    race_mod = RaceModification(
        change_type=extracted.change_type,
        new_race_date=new_race_date,
        new_distance_km=extracted.new_distance_km,
        new_priority=extracted.new_priority,
        new_taper_weeks=extracted.new_taper_weeks,
        reason=extracted.reason,
    )

    logger.info(
        "Converted extracted attributes to RaceModification",
        change_type=race_mod.change_type,
        new_race_date=race_mod.new_race_date,
        new_distance_km=race_mod.new_distance_km,
        new_priority=race_mod.new_priority,
        new_taper_weeks=race_mod.new_taper_weeks,
    )

    return race_mod
