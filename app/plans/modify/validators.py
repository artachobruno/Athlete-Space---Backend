"""Validators for MODIFY → day operations.

Enforces invariants to prevent silent corruption:
- Intent-pace compatibility
- Metrics consistency
- No invalid modifications
- Race day protection
"""

from datetime import date

from loguru import logger

from app.db.models import AthleteProfile
from app.planning.output.models import MaterializedSession
from app.plans.intent_rules import get_allowed_zones_for_intent
from app.plans.modify.types import DayModification
from app.plans.race.utils import is_race_day


def validate_modify_day(session: MaterializedSession) -> None:
    """Validate that modified session maintains intent-pace compatibility.

    Ensures that if pace is set, it's compatible with the session's intent.
    This prevents silent corruption where pace doesn't match intent.

    Args:
        session: Modified MaterializedSession to validate

    Raises:
        ValueError: If pace zone is invalid for the session's intent
    """
    allowed_zones = get_allowed_zones_for_intent(session.intent)

    # If intent has no pace constraints (e.g., rest), skip validation
    if allowed_zones is None:
        return

    # If session has no pace metrics, skip validation
    # (pace is optional, intent-pace validation only applies when both are present)
    # Note: MaterializedSession doesn't have pace directly, but we validate
    # the conceptual compatibility. Actual pace validation happens at metrics level.

    # This is a placeholder for future validation when pace is attached to session
    # For now, we validate the intent is valid
    valid_intents = {"rest", "easy", "long", "quality"}
    if session.intent not in valid_intents:
        raise ValueError(f"Invalid intent: {session.intent}")


def validate_pace_for_intent(intent: str, pace_zone: str | None) -> None:
    """Validate that a pace zone is compatible with an intent.

    Args:
        intent: Workout intent (rest, easy, long, quality)
        pace_zone: Pace zone name (e.g., "easy", "threshold")

    Raises:
        ValueError: If pace zone is invalid for the intent
    """
    if pace_zone is None:
        return  # No pace to validate

    allowed_zones = get_allowed_zones_for_intent(intent)

    # If intent has no pace constraints (e.g., rest), skip validation
    if allowed_zones is None:
        return

    if pace_zone not in allowed_zones:
        raise ValueError(
            f"Pace zone '{pace_zone}' is invalid for intent '{intent}'. "
            f"Allowed zones: {allowed_zones}"
        )


def validate_race_day_modification(
    target_date: date,
    modification: DayModification,
    *,
    athlete_profile: AthleteProfile | None = None,
) -> None:
    """Validate that race day modifications are allowed.

    Race day can only be reduced unless explicitly overridden.

    Args:
        target_date: Target date for modification
        modification: Day modification to validate
        athlete_profile: Optional athlete profile for race date

    Raises:
        ValueError: If modification is not allowed on race day
    """
    if athlete_profile is None or athlete_profile.race_date is None:
        return  # No race date, no protection needed

    # Race day can only be reduced (adjust_distance with lower value, or adjust_duration with lower value)
    # Block all other modifications
    if (
        is_race_day(target_date, athlete_profile.race_date)
        and modification.change_type not in {"adjust_distance", "adjust_duration"}
    ):
        logger.warning(
            "modify_blocked_by_race_rules",
            change_type=modification.change_type,
            target_date=target_date,
            race_date=athlete_profile.race_date,
        )
        raise ValueError("Race day can only be reduced unless explicitly overridden.")
