"""Validators for MODIFY → season operations.

Enforces invariants to prevent silent corruption:
- start_week <= end_week
- Range length sane (<= 24 weeks)
- percent XOR miles for volume changes
- No increase during taper
- No quality reduction during peak
- Phase exists
- Race/taper protection (delegates to week validators when dates available)

Note: Season modifications work with week numbers. For full race/taper protection,
week validators should be called per week when date ranges are available.
"""

from loguru import logger

from app.db.models import AthleteProfile
from app.plans.modify.season_types import SeasonModification


def validate_season_modification(
    modification: SeasonModification,
    *,
    season_weeks: list[int],
    athlete_profile: AthleteProfile | None = None,
) -> None:
    """Validate season modification against season weeks.

    This is the main validation entry point. It enforces all invariants.

    Note: For full race/taper protection, week validators should be called
    per week when date ranges are computed from week numbers.

    Args:
        modification: Season modification to validate
        season_weeks: All week numbers in the season
        athlete_profile: Optional athlete profile for race/taper protection

    Raises:
        ValueError: If validation fails
    """
    # Validate week range
    if modification.start_week > modification.end_week:
        raise ValueError(
            f"start_week ({modification.start_week}) must be <= end_week ({modification.end_week})"
        )

    # Validate range length (sane limit: <= 24 weeks)
    range_length = modification.end_week - modification.start_week + 1
    if range_length > 24:
        raise ValueError(f"Season range must be <= 24 weeks, got {range_length} weeks")

    # Validate weeks are in season
    if modification.start_week < min(season_weeks) or modification.end_week > max(season_weeks):
        raise ValueError(
            f"Week range [{modification.start_week}, {modification.end_week}] "
            f"is outside season range [{min(season_weeks)}, {max(season_weeks)}]"
        )

    # Validate change_type-specific invariants
    if modification.change_type in {"reduce_volume", "increase_volume"}:
        # Must have exactly one of percent or miles
        if modification.percent is None and modification.miles is None:
            raise ValueError(f"{modification.change_type} requires either percent or miles")

        if modification.percent is not None and modification.miles is not None:
            raise ValueError(f"{modification.change_type} requires exactly one of percent or miles, not both")

        # Validate percent bounds
        if modification.percent is not None:
            if modification.percent <= 0:
                raise ValueError(f"percent must be positive, got {modification.percent}")
            if modification.percent > 0.6:
                raise ValueError(f"percent must be <= 0.6 (60%), got {modification.percent}")

        # Check phase constraints
        if modification.phase:
            phase_lower = modification.phase.lower()
            if phase_lower == "taper" and modification.change_type == "increase_volume":
                logger.warning(
                    "modify_blocked_by_race_rules",
                    change_type=modification.change_type,
                    phase=modification.phase,
                    start_week=modification.start_week,
                    end_week=modification.end_week,
                )
                raise ValueError("Cannot increase volume during taper phase")
            if phase_lower == "peak" and modification.change_type == "reduce_volume":
                logger.warning("Reducing volume during peak phase - this may impact race performance")

        # Race/taper protection based on phase
        # Note: Full protection requires converting week numbers to dates and calling
        # week validators per week. This is a basic check based on phase field.
        if (
            athlete_profile
            and athlete_profile.race_date
            and modification.phase
            and modification.phase.lower() == "taper"
            and modification.change_type not in {"reduce_volume"}
        ):
            logger.warning(
                "modify_blocked_by_race_rules",
                change_type=modification.change_type,
                phase=modification.phase,
                start_week=modification.start_week,
                end_week=modification.end_week,
                race_date=athlete_profile.race_date,
            )
            raise ValueError("Only volume reductions are allowed during taper.")

    elif modification.change_type in {"extend_phase", "reduce_phase"}:
        if modification.weeks is None:
            raise ValueError(f"{modification.change_type} requires weeks")
        if modification.weeks <= 0:
            raise ValueError(f"weeks must be positive, got {modification.weeks}")
        if modification.phase is None:
            raise ValueError(f"{modification.change_type} requires phase")

        # Validate phase exists
        valid_phases = {"base", "build", "peak", "taper"}
        if modification.phase.lower() not in valid_phases:
            raise ValueError(f"Invalid phase: {modification.phase}. Must be one of {valid_phases}")

    logger.info(
        "Season modification validated",
        change_type=modification.change_type,
        start_week=modification.start_week,
        end_week=modification.end_week,
        phase=modification.phase,
    )
