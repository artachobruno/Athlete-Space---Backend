"""MODIFY → day tool.

Modifies a single planned workout day.
Intent is preserved unless explicitly overridden.
Never calls plan_day, never infers intent, never touches other days.
"""

import copy
from datetime import date, datetime, timezone

from loguru import logger

from app.db.models import AthleteProfile, PlannedSession
from app.db.session import get_session as _get_session
from app.plans.modify.repository import get_planned_session_by_date, save_modified_session
from app.plans.modify.types import DayModification
from app.plans.modify.validators import validate_pace_for_intent, validate_race_day_modification
from app.plans.pace import estimate_pace
from app.plans.revision import PlanRevisionBuilder
from app.plans.types import WorkoutMetrics
from app.plans.validators import validate_workout_metrics
from app.plans.week_planner import infer_intent_from_session_type

# Re-export for testability
get_session = _get_session


def modify_day(
    context: dict,
    *,
    athlete_profile: AthleteProfile | None = None,
) -> dict:
    """Modify a single planned workout day.

    This tool modifies exactly one existing planned session.
    It never regenerates, never deletes, and preserves intent by default.

    Required context fields:
        - user_id: User ID
        - athlete_id: Athlete ID
        - target_date: Target date (YYYY-MM-DD or date object)
        - modification: DayModification dict

    Args:
        context: Context dictionary with required fields
        athlete_profile: Optional athlete profile for race day protection.
            If None, race day protection is skipped. Should be fetched by
            orchestrator and passed down (tools do not access DB).

    Returns:
        Dictionary with:
            - success: bool
            - message: str
            - modified_session_id: str (if successful)
            - original_session_id: str (if successful)
            - revision: PlanRevision (canonical truth of changes)
            - error: str (if failed)

    Raises:
        ValueError: If required fields missing or invalid modification
    """
    # 1. Validate required fields
    required_fields = ["user_id", "athlete_id", "target_date", "modification"]
    missing_fields = [field for field in required_fields if field not in context]
    if missing_fields:
        raise ValueError(f"Missing required fields: {missing_fields}")

    user_id = context["user_id"]
    athlete_id = context["athlete_id"]
    target_date = context["target_date"]
    modification_dict = context["modification"]

    # Parse target_date if string
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    # Parse modification
    modification = DayModification(**modification_dict)

    # Get user request from context or use default
    user_request = context.get("user_request", f"Modify session on {target_date.isoformat()}")

    # Initialize PlanRevision builder
    builder = PlanRevisionBuilder(scope="day", user_request=user_request)
    builder.set_reason(modification.reason)
    builder.set_range(target_date.isoformat(), target_date.isoformat())

    logger.info(
        "MODIFY → day: Starting modification",
        user_id=user_id,
        athlete_id=athlete_id,
        target_date=target_date.isoformat(),
        change_type=modification.change_type,
    )

    # Validate race day protection (athlete_profile passed from orchestrator)
    try:
        validate_race_day_modification(
            target_date=target_date,
            modification=modification,
            athlete_profile=athlete_profile,
        )
        # Record rule check (not triggered)
        builder.add_rule(
            rule_id="RACE_DAY_PROTECTION",
            description="Race day can only be reduced unless explicitly overridden",
            severity="block",
            triggered=False,
        )
    except ValueError as e:
        # Record rule check (triggered - blocked)
        builder.add_rule(
            rule_id="RACE_DAY_PROTECTION",
            description="Race day can only be reduced unless explicitly overridden",
            severity="block",
            triggered=True,
        )
        revision = builder.finalize()
        return {
            "success": False,
            "error": f"Invalid modification: {e}",
            "revision": revision,
        }

    # 2. Fetch the target session deterministically
    original_session = get_planned_session_by_date(
        athlete_id=athlete_id,
        target_date=target_date,
        user_id=user_id,
    )

    if original_session is None:
        revision = builder.finalize()
        return {
            "success": False,
            "error": f"No planned session found for date {target_date.isoformat()}",
            "revision": revision,
        }

    logger.info(
        "MODIFY → day: Found target session",
        session_id=original_session.id,
        session_type=original_session.session_type,
    )

    # 3. Clone the session (never modify in place)
    new_session = copy.deepcopy(original_session)

    # Attach provenance
    # Note: PlannedSession doesn't have modified_from_session_id field yet
    # We'll store it in notes for now
    modification_note = f"[Modified from {original_session.id}]"
    if modification.reason:
        modification_note += f": {modification.reason}"
    new_session.notes = (
        f"{new_session.notes or ''}\n{modification_note}".strip()
        if new_session.notes
        else modification_note
    )

    # 4. Apply the modification (metrics only)
    # Convert PlannedSession to WorkoutMetrics for modification
    # Then convert back to PlannedSession fields

    if modification.change_type == "adjust_distance":
        if new_session.distance_mi is None:
            revision = builder.finalize()
            return {
                "success": False,
                "error": "Cannot adjust distance: session is duration-based",
                "revision": revision,
            }
        if not isinstance(modification.value, (int, float)):
            revision = builder.finalize()
            return {
                "success": False,
                "error": f"Invalid distance value: {modification.value}",
                "revision": revision,
            }
        old_distance = new_session.distance_mi
        new_session.distance_mi = float(modification.value)
        # Record delta
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="distance_mi",
            old=old_distance,
            new=new_session.distance_mi,
        )
        # Recalculate duration if pace is known (optional enhancement)

    elif modification.change_type == "adjust_duration":
        if new_session.duration_minutes is None:
            revision = builder.finalize()
            return {
                "success": False,
                "error": "Cannot adjust duration: session is distance-based",
                "revision": revision,
            }
        if not isinstance(modification.value, (int, float)):
            revision = builder.finalize()
            return {
                "success": False,
                "error": f"Invalid duration value: {modification.value}",
                "revision": revision,
            }
        old_duration = new_session.duration_minutes
        new_session.duration_minutes = int(modification.value)
        # Record delta
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="duration_minutes",
            old=old_duration,
            new=new_session.duration_minutes,
        )
        # Recalculate distance if pace is known (optional enhancement)

    elif modification.change_type == "adjust_pace":
        if not isinstance(modification.value, str):
            return {
                "success": False,
                "error": f"Invalid pace zone: {modification.value}",
            }

        # Estimate pace (athlete_profile passed from orchestrator if available)
        # For now, use a default race pace if not available
        # TODO: Get from AthletePaceProfile when available
        race_pace = 8.0  # Default 8 min/mile
        pace_metrics = estimate_pace(
            zone=modification.value,
            race_pace=race_pace,
            pace_source="training_estimate",
        )

        # Store pace zone in session (PlannedSession doesn't have pace field yet)
        # We'll store it in notes or a metadata field for now
        pace_note = f"[Pace: {pace_metrics.zone} @ {pace_metrics.pace_min_per_mile:.2f} min/mile]"
        new_session.notes = (
            f"{new_session.notes or ''}\n{pace_note}".strip()
            if new_session.notes
            else pace_note
        )

        # Validate pace for intent
        # Use intent from session (authoritative field)
        session_intent = new_session.intent or original_session.intent
        if session_intent is None:
            # Fallback: infer from session_type (legacy)
            session_intent = infer_intent_from_session_type(new_session.session_type or "easy")
            # Set it for future use
            new_session.intent = session_intent

        try:
            validate_pace_for_intent(session_intent, modification.value)
            # Record rule check (not triggered)
            builder.add_rule(
                rule_id="PACE_INTENT_COMPATIBILITY",
                description=f"Pace zone must be compatible with intent '{session_intent}'",
                severity="block",
                triggered=False,
            )
        except ValueError as e:
            # Record rule check (triggered - blocked)
            builder.add_rule(
                rule_id="PACE_INTENT_COMPATIBILITY",
                description=f"Pace zone must be compatible with intent '{session_intent}'",
                severity="block",
                triggered=True,
            )
            revision = builder.finalize()
            return {
                "success": False,
                "error": str(e),
                "revision": revision,
            }
        # Record pace change delta
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="pace_zone",
            old=None,  # Original pace not stored
            new=modification.value,
        )

    elif modification.change_type == "replace_metrics":
        if not isinstance(modification.value, dict):
            return {
                "success": False,
                "error": f"Invalid metrics dict: {modification.value}",
            }

        # Create WorkoutMetrics from dict
        try:
            new_metrics = WorkoutMetrics(**modification.value)
            validate_workout_metrics(new_metrics)

            # Apply to session
            # Note: Explicit conversion from WorkoutMetrics.distance_miles to PlannedSession.distance_mi
            # This is intentional - WorkoutMetrics uses distance_miles, PlannedSession uses distance_mi
            if new_metrics.primary == "distance":
                new_session.distance_mi = new_metrics.distance_miles
            elif new_metrics.primary == "duration":
                new_session.duration_minutes = new_metrics.duration_min

        except Exception as e:
            return {
                "success": False,
                "error": f"Invalid metrics: {e}",
            }

    # 5. Intent handling (critical)
    if modification.explicit_intent_change is not None:
        # Intent change explicitly requested
        old_intent = original_session.intent
        new_session.intent = modification.explicit_intent_change
        # Record intent change delta
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="intent",
            old=old_intent,
            new=new_session.intent,
        )
    else:
        # Intent is preserved automatically - copy from original
        new_session.intent = original_session.intent

    # 6. Validate the modified session
    # Note: Full validation would require converting to MaterializedSession
    # For now, basic validation

    # 7. Persist as a new planned session
    try:
        saved_session = save_modified_session(
            original_session=original_session,
            modified_session=new_session,
            modification_reason=modification.reason,
        )

        # Record session creation delta
        builder.add_delta(
            entity_type="session",
            entity_id=saved_session.id,
            date=target_date.isoformat(),
            field="session_created",
            old=original_session.id,
            new=saved_session.id,
        )

        logger.info(
            "MODIFY → day: Modification successful",
            original_id=original_session.id,
            new_id=saved_session.id,
        )
    except Exception as e:
        logger.exception("MODIFY → day: Failed to save modified session")
        revision = builder.finalize()
        return {
            "success": False,
            "error": f"Failed to save modified session: {e}",
            "revision": revision,
        }
    else:
        revision = builder.finalize()
        return {
            "success": True,
            "message": "Session modified successfully",
            "modified_session_id": saved_session.id,
            "original_session_id": original_session.id,
            "revision": revision,
        }
