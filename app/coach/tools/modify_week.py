"""MODIFY → week tool.

Modifies a week range of planned workouts.
Intent distribution is preserved unless explicitly overridden.
Never calls plan_week, never infers intent, never touches other weeks.
"""

import copy
from datetime import date, datetime, timezone

from loguru import logger

from app.coach.tools.modify_day import modify_day
from app.db.models import AthleteProfile, PlannedSession
from app.db.session import get_session as _get_session
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
from app.plans.revision import PlanRevisionBuilder

# Re-export for testability
get_session = _get_session

MIN_LONG_DISTANCE_MILES = 8.0  # Minimum long run distance


def modify_week(
    *,
    user_id: str,
    athlete_id: int,
    modification: WeekModification,
    user_request: str | None = None,
    athlete_profile: AthleteProfile | None = None,
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
        return {
            "success": False,
            "error": f"Invalid date format: {e}",
            "revision": revision,
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
        return {
            "success": False,
            "error": f"Invalid modification: {e}",
            "revision": revision,
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
                return {
                    "success": False,
                    "error": result.get("error", "Unknown error"),
                    "revision": revision,
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
                revision = builder.finalize()
                return {
                    "success": True,
                    "message": result.get("message", "Session modified successfully"),
                    "modified_sessions": [modified_session_id],
                    "revision": revision,
                }

            revision = builder.finalize()
            return {
                **result,
                "revision": revision,
            }
        else:
            revision = builder.finalize()
            return {
                "success": False,
                "error": f"Unknown change_type: {modification.change_type}",
                "revision": revision,
            }

        # Record deltas for all modified sessions
        for original, modified in zip(original_sessions, modified_sessions, strict=False):
            # Record session creation
            builder.add_delta(
                entity_type="session",
                entity_id=modified.id,
                date=modified.date.date().isoformat(),
                field="session_created",
                old=original.id,
                new=modified.id,
            )

            # Record field changes
            if original.distance_mi != modified.distance_mi:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=modified.date.date().isoformat(),
                    field="distance_mi",
                    old=original.distance_mi,
                    new=modified.distance_mi,
                )
            if original.duration_minutes != modified.duration_minutes:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=modified.date.date().isoformat(),
                    field="duration_minutes",
                    old=original.duration_minutes,
                    new=modified.duration_minutes,
                )
            if original.intent != modified.intent:
                builder.add_delta(
                    entity_type="session",
                    entity_id=modified.id,
                    date=modified.date.date().isoformat(),
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

        revision = builder.finalize()
        return {
            "success": True,
            "message": f"Modified {len(saved_sessions)} sessions",
            "modified_sessions": [s.id for s in saved_sessions],
            "revision": revision,
        }

    except Exception as e:
        logger.exception("MODIFY → week: Failed to apply modification")
        revision = builder.finalize()
        return {
            "success": False,
            "error": f"Failed to apply modification: {e}",
            "revision": revision,
        }


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
    current_volume = sum(s.distance_mi or 0.0 for s in original_sessions if s.distance_mi)

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
            easy_volume = sum(s.distance_mi or 0.0 for s in easy_sessions if s.distance_mi)
            if easy_volume > 0:
                easy_scale = 1.0 + (remaining_delta / easy_volume)
            else:
                easy_scale = 1.0

        # Ensure scale is non-negative (safety floor)
        easy_scale = max(0.1, easy_scale)

        for session in easy_sessions:
            cloned = clone_session(session)
            if cloned.distance_mi:
                cloned.distance_mi *= easy_scale
                # Recalculate duration if pace is known (optional enhancement)
            modified_sessions.append(cloned)

        # Easy sessions have absorbed the reduction/increase
        # Remaining delta is zero for v1 (easy-only modification)
        remaining_delta = 0

    # Apply remaining delta to long sessions (if any)
    if long_sessions and remaining_delta != 0:
        long_volume = sum(s.distance_mi or 0.0 for s in long_sessions if s.distance_mi)
        if long_volume > 0:
            long_scale = 1.0 + (remaining_delta / long_volume)
            # Ensure long run stays above minimum
            min_long_scale = MIN_LONG_DISTANCE_MILES / max(long_sessions[0].distance_mi or 0.0, MIN_LONG_DISTANCE_MILES)
            long_scale = max(min_long_scale, long_scale)

            for session in long_sessions:
                cloned = clone_session(session)
                if cloned.distance_mi is not None:
                    distance_mi = cloned.distance_mi * long_scale
                    # Ensure minimum long distance
                    cloned.distance_mi = max(distance_mi, MIN_LONG_DISTANCE_MILES)
                modified_sessions.append(cloned)

    # Sort by date to maintain order
    modified_sessions.sort(key=lambda s: s.date)

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
        session_date = original_session.date.date()

        if session_date in shift_map_parsed:
            # This session is being shifted
            new_date = shift_map_parsed[session_date]
            cloned = clone_session(original_session)

            # Update date
            new_datetime = datetime.combine(new_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            cloned.date = new_datetime

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

    # Sort by date
    modified_sessions.sort(key=lambda s: s.date)

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
