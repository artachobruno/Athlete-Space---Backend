"""MODIFY → week tool.

Modifies a week range of planned workouts.
Intent distribution is preserved unless explicitly overridden.
Never calls plan_week, never infers intent, never touches other weeks.
"""

import asyncio
import copy
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import update

from app.coach.diff.confidence import compute_revision_confidence, requires_approval
from app.coach.diff.plan_diff import build_plan_diff
from app.coach.explainability import explain_plan_revision
from app.coach.tools.modify_day import modify_day
from app.db.models import AthleteProfile, PlannedSession
from app.db.schema_v2_map import combine_date_time, mi_to_meters, minutes_to_seconds
from app.db.session import get_session as _get_session
from app.plans.modify.plan_revision_repo import create_plan_revision
from app.plans.modify.repository import get_planned_session_by_date
from app.plans.modify.types import DayModification
from app.plans.modify.week_repository import (
    clone_session,
    get_planned_sessions_in_range,
    save_modified_sessions,
)
from app.plans.modify.week_types import WeekModification
from app.plans.modify.week_validators import validate_week_modification
from app.plans.race.utils import is_race_week, is_taper_week
from app.plans.regenerate.regeneration_service import regenerate_plan
from app.plans.regenerate.types import RegenerationRequest
from app.plans.revision.builder import PlanRevisionBuilder

# Re-export for testability
get_session = _get_session

MIN_LONG_DISTANCE_MILES = 8.0  # Minimum long run distance


def _generate_explanation_sync(
    revision,
    athlete_profile: AthleteProfile | None = None,
) -> dict | None:
    """Generate explanation for a revision (sync wrapper for async function).

    Args:
        revision: PlanRevision object
        athlete_profile: Optional athlete profile

    Returns:
        Explanation dict or None if generation fails
    """
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
        return explanation.model_dump() if explanation else None
    except Exception as e:
        logger.warning(
            "Failed to generate explanation for revision",
            revision_id=revision.revision_id,
            error=str(e),
        )
        return None


def modify_week(
    *,
    user_id: str,
    athlete_id: int,
    modification: WeekModification,
    user_request: str | None = None,
    athlete_profile: AthleteProfile | None = None,
    auto_regenerate: bool = False,
) -> dict:
    """Modify a week range of planned workouts.

    This tool modifies existing planned sessions in a date range.
    It never regenerates, never deletes, and preserves intent by default.

    Required context:
        - user_id: User ID
        - athlete_id: Athlete ID
        - modification: WeekModification dict

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        modification: WeekModification object
        user_request: Optional user request text for revision tracking
        athlete_profile: Optional athlete profile for race/taper protection.
            If None, race/taper protection is skipped. Should be fetched by
            orchestrator and passed down (tools do not access DB).
        auto_regenerate: If True, automatically trigger plan regeneration
            after successful modification. Defaults to False.

    Returns:
        Dictionary with:
            - success: bool
            - message: str
            - modified_sessions: list[str] (session IDs if successful)
            - revision: PlanRevision (canonical truth of changes)
            - error: str (if failed)

    Raises:
        ValueError: If required fields missing or invalid modification
    """
    # Get user request from parameter or use default
    if user_request is None:
        user_request = f"Modify week {modification.start_date} to {modification.end_date}"

    # Initialize PlanRevision builder
    builder = PlanRevisionBuilder(scope="week", user_request=user_request)
    builder.set_reason(modification.reason)

    logger.info(
        "modify_week_started",
        user_id=user_id,
        athlete_id=athlete_id,
        change_type=modification.change_type,
        range_start=modification.start_date,
        range_end=modification.end_date,
        reason=modification.reason,
    )

    # Parse dates
    try:
        start_date = date.fromisoformat(modification.start_date)
        end_date = date.fromisoformat(modification.end_date)
        builder.set_range(modification.start_date, modification.end_date)
    except ValueError as e:
        revision = builder.finalize()

        # Persist blocked revision
        with get_session() as db:
            create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_week",
                status="blocked",
                reason=modification.reason,
                blocked_reason=f"Invalid date format: {e}",
                affected_start=start_date if "start_date" in locals() else None,
                affected_end=end_date if "end_date" in locals() else None,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        explanation = _generate_explanation_sync(revision, athlete_profile)
        return {
            "success": False,
            "error": f"Invalid date format: {e}",
            "revision": revision,
            "explanation": explanation,
        }

    # Fetch sessions in range
    original_sessions = get_planned_sessions_in_range(
        athlete_id=athlete_id,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )

    # Validate modification and record rules (athlete_profile passed from orchestrator)
    try:
        validate_week_modification(modification, original_sessions, athlete_profile=athlete_profile)
        # Record rule checks based on validation
        if athlete_profile and athlete_profile.race_date:
            race_date = athlete_profile.race_date
            taper_weeks = athlete_profile.taper_weeks or 2

            # Check race week rule
            if is_race_week(start_date, end_date, race_date):
                if modification.change_type == "increase_volume":
                    builder.add_rule(
                        rule_id="RACE_WEEK_NO_INCREASE",
                        description="Cannot increase volume during race week",
                        severity="block",
                        triggered=False,  # Validation passed, so not triggered
                    )
                else:
                    builder.add_rule(
                        rule_id="RACE_WEEK_NO_INCREASE",
                        description="Cannot increase volume during race week",
                        severity="block",
                        triggered=False,
                    )

            # Check taper week rule
            if is_taper_week(start_date, race_date, taper_weeks):
                if modification.change_type not in {"reduce_volume"}:
                    # This should have been caught by validation, but record it
                    builder.add_rule(
                        rule_id="TAPER_ONLY_REDUCTIONS",
                        description="Cannot add volume or quality sessions during taper",
                        severity="block",
                        triggered=False,  # Validation passed
                    )
                else:
                    builder.add_rule(
                        rule_id="TAPER_ONLY_REDUCTIONS",
                        description="Cannot add volume or quality sessions during taper",
                        severity="block",
                        triggered=False,
                    )
    except ValueError as e:
        # Record blocking rule
        error_msg = str(e)
        if "race week" in error_msg.lower():
            builder.add_rule(
                rule_id="RACE_WEEK_NO_INCREASE",
                description="Cannot increase volume during race week",
                severity="block",
                triggered=True,
            )
        elif "taper" in error_msg.lower():
            builder.add_rule(
                rule_id="TAPER_ONLY_REDUCTIONS",
                description="Cannot add volume or quality sessions during taper",
                severity="block",
                triggered=True,
            )
        elif "race day" in error_msg.lower():
            builder.add_rule(
                rule_id="RACE_DAY_NO_SHIFT",
                description="Race day cannot be shifted unless explicitly requested",
                severity="block",
                triggered=True,
            )
        else:
            # Generic validation error
            builder.add_rule(
                rule_id="WEEK_VALIDATION",
                description=error_msg,
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
                revision_type="modify_week",
                status="blocked",
                reason=modification.reason,
                blocked_reason=str(e),
                affected_start=start_date,
                affected_end=end_date,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        explanation = _generate_explanation_sync(revision, athlete_profile)
        return {
            "success": False,
            "error": f"Invalid modification: {e}",
            "revision": revision,
            "explanation": explanation,
        }

    # Apply modification based on change_type
    try:
        if modification.change_type in {"reduce_volume", "increase_volume"}:
            modified_sessions = _apply_volume_modification(
                original_sessions=original_sessions,
                modification=modification,
            )
        elif modification.change_type == "shift_days":
            modified_sessions = _apply_shift_modification(
                original_sessions=original_sessions,
                modification=modification,
            )
        elif modification.change_type == "replace_day":
            # Delegate to modify_day
            result = _apply_replace_day_modification(
                user_id=user_id,
                athlete_id=athlete_id,
                modification=modification,
                athlete_profile=athlete_profile,
            )

            # Normalize response - replace_day returns from modify_day directly
            # but we still want consistent logging and response shape
            if not result.get("success"):
                # Merge revision from modify_day if present
                if "revision" in result:
                    # Merge deltas from modify_day revision into week revision
                    day_revision = result["revision"]
                    for delta in day_revision.deltas:
                        builder.add_delta(
                            entity_type=delta.entity_type,
                            entity_id=delta.entity_id,
                            date=delta.date,
                            field=delta.field,
                            old=delta.old,
                            new=delta.new,
                        )
                revision = builder.finalize()

                # Persist blocked revision (replace_day failed)
                with get_session() as db:
                    create_plan_revision(
                        session=db,
                        user_id=user_id,
                        athlete_id=athlete_id,
                        revision_type="modify_week",
                        status="blocked",
                        reason=modification.reason,
                        blocked_reason=result.get("error", "Unknown error"),
                        affected_start=start_date,
                        affected_end=end_date,
                        deltas={
                            "before": None,
                            "after": None,
                            "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                        },
                    )
                    db.commit()

                explanation = _generate_explanation_sync(revision, athlete_profile)
                return {
                    "success": False,
                    "error": result.get("error", "Unknown error"),
                    "revision": revision,
                    "explanation": explanation,
                }

            # Merge revision from modify_day
            if "revision" in result:
                day_revision = result["revision"]
                for delta in day_revision.deltas:
                    builder.add_delta(
                        entity_type=delta.entity_type,
                        entity_id=delta.entity_id,
                        date=delta.date,
                        field=delta.field,
                        old=delta.old,
                        new=delta.new,
                    )

            # Log the modification (modify_day already saved the session)
            logger.info(
                "modify_week_applied",
                change_type=modification.change_type,
                range_start=modification.start_date,
                range_end=modification.end_date,
                affected_sessions=1,  # replace_day modifies one session
                original_count=len(original_sessions),
                reason=modification.reason,
            )

            # Normalize response to match other change_type responses
            # modify_day returns: {success, message, modified_session_id, original_session_id}
            # Normalize to: {success, message, modified_sessions: [...]}
            modified_session_id = result.get("modified_session_id")
            if modified_session_id:
                # Create before/after snapshots for revision
                before_snapshot = {
                    "session_count": 1,
                    "session_ids": [result.get("original_session_id")] if result.get("original_session_id") else [],
                }
                after_snapshot = {
                    "session_count": 1,
                    "session_ids": [modified_session_id],
                }

                revision = builder.finalize()

                # Persist applied revision (replace_day succeeded)
                with get_session() as db:
                    create_plan_revision(
                        session=db,
                        user_id=user_id,
                        athlete_id=athlete_id,
                        revision_type="modify_week",
                        status="applied",
                        reason=modification.reason,
                        affected_start=start_date,
                        affected_end=end_date,
                        deltas={
                            "before": before_snapshot,
                            "after": after_snapshot,
                            "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                        },
                    )
                    db.commit()

                return {
                    "success": True,
                    "message": result.get("message", "Session modified successfully"),
                    "modified_sessions": [modified_session_id],
                    "revision": revision,
                }

            revision = builder.finalize()

            # Persist applied revision (fallback case)
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_week",
                    status="applied",
                    reason=modification.reason,
                    affected_start=start_date,
                    affected_end=end_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            return {
                **result,
                "revision": revision,
            }
        else:
            revision = builder.finalize()

            # Persist blocked revision (unknown change_type)
            with get_session() as db:
                create_plan_revision(
                    session=db,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    revision_type="modify_week",
                    status="blocked",
                    reason=modification.reason,
                    blocked_reason=f"Unknown change_type: {modification.change_type}",
                    affected_start=start_date,
                    affected_end=end_date,
                    deltas={
                        "before": None,
                        "after": None,
                        "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                    },
                )
                db.commit()

            explanation = _generate_explanation_sync(revision, athlete_profile)
            return {
                "success": False,
                "error": f"Unknown change_type: {modification.change_type}",
                "revision": revision,
                "explanation": explanation,
            }

        # Record deltas for all modified sessions
        for original, modified in zip(original_sessions, modified_sessions, strict=False):
            # Schema v2: Get date from starts_at
            session_date = modified.starts_at.date() if modified.starts_at else None
            date_str = session_date.isoformat() if session_date else ""

            # Record session creation
            builder.add_delta(
                entity_type="session",
                entity_id=modified.id,
                date=date_str,
                field="session_created",
                old=original.id,
                new=modified.id,
            )

            # Record field changes (use compatibility properties for user-friendly display)
            if hasattr(original, "distance_mi") and original.distance_mi:
                original_distance_mi = original.distance_mi
            elif original.distance_meters:
                original_distance_mi = original.distance_meters / 1609.34
            else:
                original_distance_mi = None

            if hasattr(modified, "distance_mi") and modified.distance_mi:
                modified_distance_mi = modified.distance_mi
            elif modified.distance_meters:
                modified_distance_mi = modified.distance_meters / 1609.34
            else:
                modified_distance_mi = None
            if original_distance_mi != modified_distance_mi:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=date_str,
                    field="distance_mi",
                    old=original_distance_mi,
                    new=modified_distance_mi,
                )
            if hasattr(original, "duration_minutes") and original.duration_minutes:
                original_duration_min = original.duration_minutes
            elif original.duration_seconds:
                original_duration_min = original.duration_seconds // 60
            else:
                original_duration_min = None

            if hasattr(modified, "duration_minutes") and modified.duration_minutes:
                modified_duration_min = modified.duration_minutes
            elif modified.duration_seconds:
                modified_duration_min = modified.duration_seconds // 60
            else:
                modified_duration_min = None
            if original_duration_min != modified_duration_min:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=date_str,
                    field="duration_minutes",
                    old=original_duration_min,
                    new=modified_duration_min,
                )
            if original.intent != modified.intent:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=date_str,
                    field="intent",
                    old=original.intent,
                    new=modified.intent,
                )

        # Save modified sessions
        saved_sessions = save_modified_sessions(
            original_sessions=original_sessions,
            modified_sessions=modified_sessions,
            modification_reason=modification.reason,
        )

        logger.info(
            "modify_week_applied",
            change_type=modification.change_type,
            range_start=modification.start_date,
            range_end=modification.end_date,
            affected_sessions=len(saved_sessions),
            original_count=len(original_sessions),
            reason=modification.reason,
        )

        # Generate diff using diff engine
        diff = build_plan_diff(
            before_sessions=original_sessions,
            after_sessions=saved_sessions,
            scope="week",
        )

        # Compute confidence and determine if approval is required
        confidence = compute_revision_confidence(diff)
        needs_approval = requires_approval("modify_week", confidence)

        revision = builder.finalize()

        # Determine status based on approval requirement
        final_status = "pending" if needs_approval else "applied"

        # Persist applied revision
        with get_session() as db:
            revision_record = create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_week",
                status=final_status,
                reason=modification.reason,
                affected_start=start_date,
                affected_end=end_date,
                deltas={
                    "diff": diff.model_dump(),
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
                confidence=confidence,
                requires_approval=needs_approval,
            )
            db.commit()

            # Update saved sessions with revision_id
            if saved_sessions:
                session_ids = [s.id for s in saved_sessions]
                with get_session() as update_db:
                    update_db.execute(
                        update(PlannedSession)
                        .where(PlannedSession.id.in_(session_ids))
                        .values(revision_id=revision_record.id)
                    )
                    update_db.commit()

        explanation = _generate_explanation_sync(revision, athlete_profile)
        result = {
            "success": True,
            "message": f"Modified {len(saved_sessions)} sessions",
            "modified_sessions": [s.id for s in saved_sessions],
            "revision": revision,
            "explanation": explanation,
            "revision_id": revision_record.id,  # Phase 5: Include revision_id for approval enforcement
            "requires_approval": needs_approval,  # Phase 5: Include approval flag for executor check
        }

        # Optional: Trigger regeneration if requested
        if auto_regenerate:
            try:
                today = datetime.now(timezone.utc).date()
                regen_req = RegenerationRequest(
                    start_date=today,
                    mode="partial",
                    reason=f"Auto-regenerate after modify_week from {start_date.isoformat()} to {end_date.isoformat()}",
                )
                regen_revision = regenerate_plan(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    req=regen_req,
                )
                result["regeneration_revision"] = regen_revision
                logger.info(
                    "Auto-regeneration triggered after modify_week",
                    revision_id=regen_revision.id,
                )
            except Exception as e:
                logger.warning(
                    "Auto-regeneration failed after modify_week",
                    error=str(e),
                )
                # Don't fail the modify_week operation if regeneration fails
                result["regeneration_error"] = str(e)

    except Exception as e:
        logger.exception("MODIFY → week: Failed to apply modification")
        revision = builder.finalize()

        # Persist blocked revision (exception during modification)
        with get_session() as db:
            create_plan_revision(
                session=db,
                user_id=user_id,
                athlete_id=athlete_id,
                revision_type="modify_week",
                status="blocked",
                reason=modification.reason,
                blocked_reason=f"Failed to apply modification: {e}",
                affected_start=start_date,
                affected_end=end_date,
                deltas={
                    "before": None,
                    "after": None,
                    "revision": revision.model_dump() if hasattr(revision, "model_dump") else None,
                },
            )
            db.commit()

        explanation = _generate_explanation_sync(revision, athlete_profile)
        return {
            "success": False,
            "error": f"Failed to apply modification: {e}",
            "revision": revision,
            "explanation": explanation,
        }
    else:
        return result


def _apply_volume_modification(
    original_sessions: list[PlannedSession],
    modification: WeekModification,
) -> list[PlannedSession]:
    """Apply volume reduction or increase.

    Algorithm:
    1. Partition sessions by intent: quality, long, easy, rest
    2. Compute current weekly volume (miles)
    3. Determine target delta (percent or miles)
    4. Apply changes in order: easy first, then long (preserving min), then quality (not in v1)

    Args:
        original_sessions: Original sessions in range
        modification: WeekModification with volume change

    Returns:
        List of modified PlannedSession objects
    """
    # Partition by intent
    quality_sessions = [s for s in original_sessions if s.intent == "quality"]
    long_sessions = [s for s in original_sessions if s.intent == "long"]
    easy_sessions = [s for s in original_sessions if s.intent == "easy"]
    rest_sessions = [s for s in original_sessions if s.intent == "rest" or s.intent is None]

    # Compute current weekly volume (miles only)
    # Schema v2: Use compatibility property or convert from meters
    current_volume = sum(
        (s.distance_mi if hasattr(s, "distance_mi") and s.distance_mi else (s.distance_meters / 1609.34 if s.distance_meters else 0.0))
        for s in original_sessions
    )

    # Determine target delta
    if modification.percent is not None:
        delta = current_volume * modification.percent
        if modification.change_type == "reduce_volume":
            delta = -delta
    elif modification.miles is not None:
        delta = modification.miles
    else:
        raise ValueError("Volume modification requires either percent or miles")

    logger.debug(
        "Applying volume modification",
        current_volume=current_volume,
        delta=delta,
        easy_count=len(easy_sessions),
        long_count=len(long_sessions),
        quality_count=len(quality_sessions),
    )

    # Clone all sessions
    modified_sessions: list[PlannedSession] = []

    # Keep rest sessions unchanged
    modified_sessions.extend(clone_session(s) for s in rest_sessions)

    # Keep quality sessions unchanged (v1 - don't modify quality)
    modified_sessions.extend(clone_session(s) for s in quality_sessions)

    # Apply volume change to easy sessions first
    # For v1: reduce easy by the same percent as the weekly reduction
    # This means easy sessions scale by (1 - percent), not by weekly delta
    remaining_delta = delta
    if easy_sessions and remaining_delta != 0:
        # Determine scale based on modification percent (for reduce/increase_volume)
        if modification.change_type == "reduce_volume" and modification.percent is not None:
            # Easy sessions reduce by the same percent as requested
            easy_scale = 1.0 - modification.percent
        elif modification.change_type == "increase_volume" and modification.percent is not None:
            # Easy sessions increase by the same percent as requested
            easy_scale = 1.0 + modification.percent
        else:
            # Fallback to delta-based scaling for absolute miles
            def get_distance_miles(session):
                if hasattr(session, "distance_mi") and session.distance_mi:
                    return session.distance_mi
                if session.distance_meters:
                    return session.distance_meters / 1609.34
                return 0.0

            easy_volume = sum(get_distance_miles(s) for s in easy_sessions)
            if easy_volume > 0:
                easy_scale = 1.0 + (remaining_delta / easy_volume)
            else:
                easy_scale = 1.0

        # Ensure scale is non-negative (safety floor)
        easy_scale = max(0.1, easy_scale)

        for session in easy_sessions:
            cloned = clone_session(session)
            # Schema v2: Modify distance_meters (convert from miles for calculation)
            if cloned.distance_meters:
                # Get current distance in miles for calculation
                if hasattr(cloned, "distance_mi") and cloned.distance_mi:
                    current_miles = cloned.distance_mi
                else:
                    current_miles = cloned.distance_meters / 1609.34
                new_miles = current_miles * easy_scale
                cloned.distance_meters = mi_to_meters(new_miles)
            elif hasattr(cloned, "distance_mi") and cloned.distance_mi:
                # Fallback if compatibility property works
                current_miles = cloned.distance_mi
                new_miles = current_miles * easy_scale
                cloned.distance_meters = mi_to_meters(new_miles)
            # Recalculate duration if pace is known (optional enhancement)
            modified_sessions.append(cloned)

        # Easy sessions have absorbed the reduction/increase
        # Remaining delta is zero for v1 (easy-only modification)
        remaining_delta = 0

    # Apply remaining delta to long sessions (if any)
    if long_sessions and remaining_delta != 0:
        long_volume = sum(
            (s.distance_mi if hasattr(s, "distance_mi") and s.distance_mi else (s.distance_meters / 1609.34 if s.distance_meters else 0.0))
            for s in long_sessions
        )
        if long_volume > 0:
            long_scale = 1.0 + (remaining_delta / long_volume)
            # Ensure long run stays above minimum
            first_long_miles = (
                long_sessions[0].distance_mi
                if hasattr(long_sessions[0], "distance_mi") and long_sessions[0].distance_mi
                else (long_sessions[0].distance_meters / 1609.34 if long_sessions[0].distance_meters else 0.0)
            )
            min_long_scale = MIN_LONG_DISTANCE_MILES / max(first_long_miles, MIN_LONG_DISTANCE_MILES)
            long_scale = max(min_long_scale, long_scale)

            for session in long_sessions:
                cloned = clone_session(session)
                # Schema v2: Modify distance_meters
                if cloned.distance_meters is not None:
                    if hasattr(cloned, "distance_mi") and cloned.distance_mi:
                        current_miles = cloned.distance_mi
                    else:
                        current_miles = cloned.distance_meters / 1609.34
                    distance_mi = current_miles * long_scale
                    # Ensure minimum long distance
                    final_miles = max(distance_mi, MIN_LONG_DISTANCE_MILES)
                    cloned.distance_meters = mi_to_meters(final_miles)
                elif hasattr(cloned, "distance_mi") and cloned.distance_mi is not None:
                    distance_mi = cloned.distance_mi * long_scale
                    final_miles = max(distance_mi, MIN_LONG_DISTANCE_MILES)
                    cloned.distance_meters = mi_to_meters(final_miles)
                modified_sessions.append(cloned)

    # Sort by starts_at to maintain order (schema v2)
    modified_sessions.sort(key=lambda s: s.starts_at if s.starts_at else datetime.min.replace(tzinfo=timezone.utc))

    return modified_sessions


def _apply_shift_modification(
    original_sessions: list[PlannedSession],
    modification: WeekModification,
) -> list[PlannedSession]:
    """Apply day shifting.

    Rules:
    - Clone sessions to new dates
    - Old sessions remain (new ones supersede them)
    - Add notes tag: shifted_from=YYYY-MM-DD

    Args:
        original_sessions: Original sessions in range
        modification: WeekModification with shift_map

    Returns:
        List of modified PlannedSession objects
    """
    if not modification.shift_map:
        raise ValueError("shift_days requires shift_map")

    modified_sessions: list[PlannedSession] = []

    # Parse shift_map dates
    shift_map_parsed: dict[date, date] = {}
    for old_date_str, new_date_str in modification.shift_map.items():
        old_date = date.fromisoformat(old_date_str)
        new_date = date.fromisoformat(new_date_str)
        shift_map_parsed[old_date] = new_date

    # Process each original session
    for original_session in original_sessions:
        # Schema v2: Get date from starts_at
        session_date = original_session.starts_at.date() if original_session.starts_at else None
        if session_date is None:
            # Skip sessions without starts_at
            modified_sessions.append(clone_session(original_session))
            continue

        if session_date in shift_map_parsed:
            # This session is being shifted
            new_date = shift_map_parsed[session_date]
            cloned = clone_session(original_session)

            # Schema v2: Update starts_at (preserve time from original)
            original_time = original_session.starts_at.time() if original_session.starts_at else None
            time_str = original_time.strftime("%H:%M") if original_time else None
            cloned.starts_at = combine_date_time(new_date, time_str)

            # Add shift metadata to notes
            shift_note = f"[Shifted from {session_date.isoformat()}]"
            cloned.notes = (
                f"{cloned.notes or ''}\n{shift_note}".strip()
                if cloned.notes
                else shift_note
            )

            modified_sessions.append(cloned)
        else:
            # Keep unchanged
            modified_sessions.append(clone_session(original_session))

    # Sort by starts_at (schema v2)
    modified_sessions.sort(key=lambda s: s.starts_at if s.starts_at else datetime.min.replace(tzinfo=timezone.utc))

    return modified_sessions


def _apply_replace_day_modification(
    user_id: str,
    athlete_id: int,
    modification: WeekModification,
    *,
    athlete_profile: AthleteProfile | None = None,
) -> dict:
    """Apply replace_day by delegating to modify_day.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        modification: WeekModification with target_date and day_modification
        athlete_profile: Optional athlete profile for race day protection

    Returns:
        Result dictionary from modify_day
    """
    if not modification.target_date:
        return {
            "success": False,
            "error": "replace_day requires target_date",
        }

    if not modification.day_modification:
        return {
            "success": False,
            "error": "replace_day requires day_modification",
        }

    # Build DayModification from day_modification dict
    try:
        day_mod = DayModification(**modification.day_modification)
    except Exception as e:
        return {
            "success": False,
            "error": f"Invalid day_modification: {e}",
        }

    # Call modify_day (pass athlete_profile through)
    return modify_day(
        context={
            "user_id": user_id,
            "athlete_id": athlete_id,
            "target_date": modification.target_date,
            "modification": day_mod.model_dump(),
        },
        athlete_profile=athlete_profile,
    )
