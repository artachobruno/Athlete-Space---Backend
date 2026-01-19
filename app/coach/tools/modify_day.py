"""MODIFY → day tool.

Modifies a single planned workout day.
Intent is preserved unless explicitly overridden.
Never calls plan_day, never infers intent, never touches other days.
"""

import asyncio
import copy
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import update

from app.coach.diff.confidence import compute_revision_confidence, requires_approval
from app.coach.diff.plan_diff import build_plan_diff
from app.coach.explainability.revision_explainer import explain_plan_revision
from app.db.models import AthleteProfile, PlannedSession
from app.db.schema_v2_map import mi_to_meters, minutes_to_seconds
from app.db.session import get_session as _get_session
from app.plans.modify.plan_revision_repo import create_plan_revision
from app.plans.modify.repository import get_planned_session_by_date, save_modified_session
from app.plans.modify.types import DayModification
from app.plans.modify.validators import validate_pace_for_intent, validate_race_day_modification
from app.plans.pace import estimate_pace
from app.plans.regenerate.regeneration_service import regenerate_plan
from app.plans.regenerate.types import RegenerationRequest
from app.plans.revision.builder import PlanRevisionBuilder
from app.plans.types import WorkoutMetrics
from app.plans.validators import validate_workout_metrics
from app.plans.week_planner import infer_intent_from_session_type

# Re-export for testability
get_session = _get_session


def modify_day(
    context: dict,
    *,
    athlete_profile: AthleteProfile | None = None,
    auto_regenerate: bool = False,
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
        auto_regenerate: If True, automatically trigger plan regeneration
            after successful modification. Defaults to False.

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

        # Persist blocked revision
        with get_session() as db:
            create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_day",
                status="blocked",
                reason=modification.reason,
                blocked_reason=str(e),
                affected_start=target_date,
                affected_end=target_date,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        # Generate explanation for blocked revision
        explanation = None
        try:
            athlete_context = {}
            if athlete_profile and athlete_profile.race_date:
                athlete_context["race_date"] = athlete_profile.race_date

            deltas_dict = {
                "deltas": [delta.model_dump() for delta in revision.deltas],
            }
            constraints_triggered = [r.rule_id for r in revision.rules if r.triggered]

            explanation = asyncio.run(
                explain_plan_revision(
                    revision=revision,
                    deltas=deltas_dict,
                    athlete_profile=athlete_context if athlete_context else None,
                    constraints_triggered=constraints_triggered if constraints_triggered else None,
                )
            )
        except Exception as explain_error:
            logger.warning(
                "Failed to generate explanation for blocked revision",
                revision_id=revision.revision_id,
                error=str(explain_error),
            )

        return {
            "success": False,
            "error": f"Invalid modification: {e}",
            "revision": revision,
            "explanation": explanation.model_dump() if explanation else None,
        }

    # 2. Fetch the target session deterministically
    original_session = get_planned_session_by_date(
        athlete_id=athlete_id,
        target_date=target_date,
        user_id=user_id,
    )

    if original_session is None:
        revision = builder.finalize()

        # Persist blocked revision (no session found)
        with get_session() as db:
            create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_day",
                status="blocked",
                reason=modification.reason,
                blocked_reason=f"No planned session found for date {target_date.isoformat()}",
                affected_start=target_date,
                affected_end=target_date,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        # Generate explanation for blocked revision (no session found)
        explanation = None
        try:
            athlete_context = {}
            if athlete_profile and athlete_profile.race_date:
                athlete_context["race_date"] = athlete_profile.race_date

            deltas_dict = {
                "deltas": [delta.model_dump() for delta in revision.deltas],
            }
            constraints_triggered = [r.rule_id for r in revision.rules if r.triggered]

            explanation = asyncio.run(
                explain_plan_revision(
                    revision=revision,
                    deltas=deltas_dict,
                    athlete_profile=athlete_context if athlete_context else None,
                    constraints_triggered=constraints_triggered if constraints_triggered else None,
                )
            )
        except Exception as explain_error:
            logger.warning(
                "Failed to generate explanation for blocked revision",
                revision_id=revision.revision_id,
                error=str(explain_error),
            )

        return {
            "success": False,
            "error": f"No planned session found for date {target_date.isoformat()}",
            "revision": revision,
            "explanation": explanation.model_dump() if explanation else None,
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
        # Schema v2: Check distance_meters (via compatibility property for reading)
        if new_session.distance_meters is None or (hasattr(new_session, "distance_mi") and new_session.distance_mi is None):
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason="Cannot adjust distance: session is duration-based",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": "Cannot adjust distance: session is duration-based",
                "revision": revision,
            }
        if not isinstance(modification.value, (int, float)):
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Invalid distance value: {modification.value}",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": f"Invalid distance value: {modification.value}",
                "revision": revision,
            }
        # Schema v2: Store old distance (miles for delta logging, but convert to meters for DB)
        # Compatibility property handles conversion
        old_distance_mi = new_session.distance_mi if hasattr(new_session, "distance_mi") else (
            new_session.distance_meters / 1609.34 if new_session.distance_meters else None
        )
        # Modification value is in miles, convert to meters for schema v2
        new_distance_meters = mi_to_meters(float(modification.value))
        new_session.distance_meters = new_distance_meters
        # Record delta (in miles for user-friendly display)
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="distance_mi",
            old=old_distance_mi,
            new=float(modification.value),
        )
        # Recalculate duration if pace is known (optional enhancement)

    elif modification.change_type == "adjust_duration":
        # Schema v2: Check duration_seconds (via compatibility property for reading)
        if new_session.duration_seconds is None or (hasattr(new_session, "duration_minutes") and new_session.duration_minutes is None):
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason="Cannot adjust duration: session is distance-based",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": "Cannot adjust duration: session is distance-based",
                "revision": revision,
            }
        if not isinstance(modification.value, (int, float)):
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Invalid duration value: {modification.value}",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": f"Invalid duration value: {modification.value}",
                "revision": revision,
            }
        # Schema v2: Store old duration (minutes for delta logging, but convert to seconds for DB)
        # Compatibility property handles conversion
        old_duration_min = new_session.duration_minutes if hasattr(new_session, "duration_minutes") else (
            new_session.duration_seconds // 60 if new_session.duration_seconds else None
        )
        # Modification value is in minutes, convert to seconds for schema v2
        new_duration_seconds = minutes_to_seconds(int(modification.value))
        new_session.duration_seconds = new_duration_seconds
        # Record delta (in minutes for user-friendly display)
        builder.add_delta(
            entity_type="session",
            entity_id=original_session.id,
            date=target_date.isoformat(),
            field="duration_minutes",
            old=old_duration_min,
            new=int(modification.value),
        )
        # Recalculate distance if pace is known (optional enhancement)

    elif modification.change_type == "adjust_pace":
        if not isinstance(modification.value, str):
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Invalid pace zone: {modification.value}",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": f"Invalid pace zone: {modification.value}",
                "revision": revision,
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

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=str(e),
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

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
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Invalid metrics dict: {modification.value}",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": f"Invalid metrics dict: {modification.value}",
                "revision": revision,
            }

        # Create WorkoutMetrics from dict
        try:
            new_metrics = WorkoutMetrics(**modification.value)
            validate_workout_metrics(new_metrics)

            # Apply to session
            # Schema v2: Convert WorkoutMetrics to schema v2 fields
            # WorkoutMetrics uses distance_miles/duration_min, PlannedSession v2 uses distance_meters/duration_seconds
            if new_metrics.primary == "distance":
                new_session.distance_meters = mi_to_meters(new_metrics.distance_miles)
            elif new_metrics.primary == "duration":
                new_session.duration_seconds = minutes_to_seconds(new_metrics.duration_min)

        except Exception as e:
            revision = builder.finalize()

            # Persist blocked revision
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_day",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Invalid metrics: {e}",
                    affected_start=target_date,
                    affected_end=target_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                "success": False,
                "error": f"Invalid metrics: {e}",
                "revision": revision,
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

    # 7. Generate diff and compute confidence BEFORE saving
    # This allows us to check if approval is required before mutating
    diff = build_plan_diff(
        before_sessions=[original_session],
        after_sessions=[new_session],  # Use new_session, not saved_session
        scope="day",
    )

    # Compute confidence and determine if approval is required
    confidence = compute_revision_confidence(diff)
    needs_approval = requires_approval("modify_day", confidence)

    # If approval required, don't save yet - create pending revision
    if needs_approval:
        revision = builder.finalize()

        # Persist pending revision (without saving session)
        with get_session() as db:
            revision_record = create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_day",
                status="pending",
                reason=modification.reason,
                affected_start=target_date,
                affected_end=target_date,
                deltas={
                    "diff": diff.model_dump(),
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    "pending_session": {
                        "original_id": original_session.id,
                        "modified_data": {
                            "distance_mi": (
                                new_session.distance_mi
                                if hasattr(new_session, "distance_mi")
                                else (new_session.distance_meters / 1609.34 if new_session.distance_meters else None)
                            ),
                            "duration_minutes": (
                                new_session.duration_minutes
                                if hasattr(new_session, "duration_minutes")
                                else (new_session.duration_seconds // 60 if new_session.duration_seconds else None)
                            ),
                            "intent": new_session.intent,
                            "title": new_session.title,
                            "sport": new_session.sport,  # Schema v2: sport instead of type
                        },
                    },
                },
                confidence=confidence,
                requires_approval=True,
            )
            db.commit()

        return {
            "success": True,
            "message": "Modification created, pending approval",
            "revision": revision,
            "requires_approval": True,
            "revision_id": revision_record.id,
        }

    # 8. Approval not required - persist as a new planned session
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

        revision = builder.finalize()

        # Persist applied revision
        with get_session() as db:
            revision_record = create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_day",
                status="applied",
                reason=modification.reason,
                affected_start=target_date,
                affected_end=target_date,
                deltas={
                    "diff": diff.model_dump(),
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
                confidence=confidence,
                requires_approval=False,
            )
            db.commit()

            # Update saved session with revision_id
            with get_session() as update_db:
                update_db.execute(
                    update(PlannedSession)
                    .where(PlannedSession.id == saved_session.id)
                    .values(revision_id=revision_record.id)
                )
                update_db.commit()
    except Exception as e:
        logger.exception("MODIFY → day: Failed to save modified session")
        revision = builder.finalize()

        # Persist blocked revision (save failed)
        with get_session() as db:
            create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_day",
                status="blocked",
                reason=modification.reason,
                blocked_reason=f"Failed to save modified session: {e}",
                affected_start=target_date,
                affected_end=target_date,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        return {
            "success": False,
            "error": f"Failed to save modified session: {e}",
            "revision": revision,
        }
    else:
        # Generate explanation for the revision
        explanation = None
        try:
            # Build athlete context for explanation
            athlete_context = {}
            if athlete_profile and athlete_profile.race_date:
                athlete_context["race_date"] = athlete_profile.race_date
                # Add other context fields as needed

            # Build deltas dict from revision
            deltas_dict = {
                "deltas": [delta.model_dump() for delta in revision.deltas],
            }

            # Extract constraints triggered
            constraints_triggered = [r.rule_id for r in revision.rules if r.triggered]

            # Call explainability (async from sync context)
            explanation = asyncio.run(
                explain_plan_revision(
                    revision=revision,
                    deltas=deltas_dict,
                    athlete_profile=athlete_context if athlete_context else None,
                    constraints_triggered=constraints_triggered if constraints_triggered else None,
                )
            )
        except Exception as e:
            logger.warning(
                "Failed to generate revision explanation",
                revision_id=revision.revision_id,
                error=str(e),
            )
            # Don't fail the operation if explanation fails

        result = {
            "success": True,
            "message": "Session modified successfully",
            "modified_session_id": saved_session.id,
            "original_session_id": original_session.id,
            "revision": revision,
            "explanation": explanation.model_dump() if explanation else None,
        }

        # Optional: Trigger regeneration if requested
        if auto_regenerate:
            try:
                today = datetime.now(timezone.utc).date()
                regen_req = RegenerationRequest(
                    start_date=today,
                    mode="partial",
                    reason=f"Auto-regenerate after modify_day on {target_date.isoformat()}",
                )
                regen_revision = regenerate_plan(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    req=regen_req,
                )
                result["regeneration_revision"] = regen_revision
                logger.info(
                    "Auto-regeneration triggered after modify_day",
                    revision_id=regen_revision.id,
                )
            except Exception as e:
                logger.warning(
                    "Auto-regeneration failed after modify_day",
                    error=str(e),
                )
                # Don't fail the modify_day operation if regeneration fails
                result["regeneration_error"] = str(e)

        return result
