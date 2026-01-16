"""Adapter to convert extracted season modification to structured SeasonModification.

This layer bridges LLM extraction and execution.
It resolves season_ref and phase to week ranges, enforces invariants, and validates.
NO LLM calls here.
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.coach.extraction.modify_season_extractor import ExtractedSeasonModification
from app.db.models import PlannedSession, SeasonPlan
from app.db.session import get_session
from app.plans.modify.season_types import SeasonModification


def _get_season_weeks(athlete_id: int) -> list[int]:
    """Get list of week numbers for the active season plan.

    Args:
        athlete_id: Athlete ID

    Returns:
        List of week numbers (1-based) for the active season
    """
    with get_session() as db:
        # Get active season plan
        season_plan = (
            db.execute(
                select(SeasonPlan)
                .where(SeasonPlan.athlete_id == athlete_id, SeasonPlan.is_active.is_(True))
                .order_by(SeasonPlan.version.desc())
            )
            .scalar_one_or_none()
        )

        if season_plan is None:
            # Fallback: get week numbers from PlannedSession
            sessions = (
                db.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.plan_type == "season",
                        PlannedSession.completed.is_(False),
                    )
                    .distinct(PlannedSession.week_number)
                    .order_by(PlannedSession.week_number)
                )
                .scalars()
                .all()
            )

            return sorted({s.week_number for s in sessions if s.week_number is not None})

        # Get week numbers from planned sessions for this season
        sessions = (
            db.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.athlete_id == athlete_id,
                    PlannedSession.plan_type == "season",
                    PlannedSession.plan_id == season_plan.id,
                    PlannedSession.completed.is_(False),
                )
                .distinct(PlannedSession.week_number)
                .order_by(PlannedSession.week_number)
            )
            .scalars()
            .all()
        )

        week_numbers = sorted({s.week_number for s in sessions if s.week_number is not None})

        # If no sessions found, use total_weeks from season plan
        if not week_numbers and season_plan.total_weeks:
            week_numbers = list(range(1, season_plan.total_weeks + 1))

        return week_numbers


def _resolve_phase_to_weeks(phase: str, season_weeks: list[int]) -> list[int]:
    """Resolve phase name to week numbers.

    For now, this is a simple implementation. In the future, this could
    query phase metadata from the season plan or use days_to_race logic.

    Args:
        phase: Phase name (base, build, peak, taper)
        season_weeks: All week numbers in the season
        athlete_id: Athlete ID (for future phase resolution)

    Returns:
        List of week numbers in the phase
    """
    if not season_weeks:
        return []

    total_weeks = len(season_weeks)
    phase_lower = phase.lower()

    # Simple heuristic: divide season into phases
    if phase_lower == "base":
        # First 40% of season
        end_idx = int(total_weeks * 0.4)
        return season_weeks[:end_idx] if end_idx > 0 else season_weeks[:1]
    if phase_lower == "build":
        # Middle 40% of season
        start_idx = int(total_weeks * 0.4)
        end_idx = int(total_weeks * 0.8)
        return season_weeks[start_idx:end_idx] if start_idx < end_idx else season_weeks[start_idx:]
    if phase_lower == "peak":
        # 80-90% of season
        start_idx = int(total_weeks * 0.8)
        end_idx = int(total_weeks * 0.9)
        return season_weeks[start_idx:end_idx] if start_idx < end_idx else season_weeks[start_idx:]
    if phase_lower == "taper":
        # Last 10% of season
        start_idx = int(total_weeks * 0.9)
        return season_weeks[start_idx:] if start_idx < total_weeks else season_weeks[-1:]
    # Unknown phase - return all weeks
    logger.warning(f"Unknown phase: {phase}, returning all weeks")
    return season_weeks


def adapt_extracted_season_modification(
    extracted: ExtractedSeasonModification,
    *,
    athlete_id: int,
) -> SeasonModification:
    """Convert extracted attributes to structured SeasonModification.

    This function:
    - Resolves season_ref to week range (defaults to all weeks)
    - Resolves phase to subset of weeks
    - Enforces invariants (percent XOR miles for volume changes)
    - Validates required fields per change_type
    - Returns validated SeasonModification

    Args:
        extracted: Extracted attributes from LLM
        athlete_id: Athlete ID for season resolution

    Returns:
        SeasonModification ready for execution

    Raises:
        ValueError: If validation fails or required fields missing
    """
    if extracted.change_type is None:
        raise ValueError("change_type is required but was not extracted")

    # Get all season weeks
    season_weeks = _get_season_weeks(athlete_id)

    if not season_weeks:
        raise ValueError("No season plan found or no weeks available")

    # Resolve week range
    start_week: int
    end_week: int

    if extracted.phase:
        # Phase-based resolution
        phase_weeks = _resolve_phase_to_weeks(extracted.phase, season_weeks)
        if not phase_weeks:
            raise ValueError(f"No weeks found for phase: {extracted.phase}")
        start_week = min(phase_weeks)
        end_week = max(phase_weeks)
    elif extracted.season_ref:
        # Season reference - for now, default to all weeks
        # In the future, could resolve "this season", "spring build", etc.
        logger.info(f"Season reference '{extracted.season_ref}' resolved to all weeks")
        start_week = min(season_weeks)
        end_week = max(season_weeks)
    else:
        # Default to all weeks
        start_week = min(season_weeks)
        end_week = max(season_weeks)

    # Clamp bounds
    start_week = max(1, start_week)
    end_week = min(max(season_weeks), end_week)

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

    elif extracted.change_type in {"extend_phase", "reduce_phase"}:
        if extracted.weeks is None:
            raise ValueError(f"{extracted.change_type} requires weeks")
        if extracted.weeks <= 0:
            raise ValueError(f"weeks must be positive, got {extracted.weeks}")

    # Build SeasonModification
    season_mod = SeasonModification(
        change_type=extracted.change_type,
        start_week=start_week,
        end_week=end_week,
        phase=extracted.phase,
        percent=extracted.percent,
        miles=extracted.miles,
        weeks=extracted.weeks,
        reason=extracted.reason,
    )

    logger.info(
        "Converted extracted attributes to SeasonModification",
        change_type=season_mod.change_type,
        start_week=season_mod.start_week,
        end_week=season_mod.end_week,
        phase=season_mod.phase,
    )

    return season_mod
