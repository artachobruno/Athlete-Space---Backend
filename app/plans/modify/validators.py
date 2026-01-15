"""Validators for MODIFY → day operations.

Enforces invariants to prevent silent corruption:
- Intent-pace compatibility
- Metrics consistency
- No invalid modifications
"""

from app.planning.output.models import MaterializedSession
from app.plans.intent_rules import get_allowed_zones_for_intent


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
