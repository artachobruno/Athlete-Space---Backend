"""Auto-pairing service for deterministic pairing of planned sessions and activities.

This service implements the canonical pairing logic:
- Same user
- Same day
- Same activity type (normalized)
- Duration within ±30%
- If multiple candidates → closest duration wins (deterministic)

All pairing decisions are logged to pairing_decisions table for auditability.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PairingDecision, PlannedSession
from app.pairing.session_links import get_link_for_activity, get_link_for_planned, upsert_link
from app.plans.reconciliation.service import reconcile_activity_if_paired
from app.workouts.workout_factory import WorkoutFactory

DURATION_TOLERANCE = 0.30


def _normalize_activity_type(activity_type: str | None) -> str | None:
    """Normalize activity type for comparison.

    Handles case-insensitive matching and common variations.

    Args:
        activity_type: Activity type (may be None)

    Returns:
        Normalized type string or None
    """
    if not activity_type:
        return None

    normalized = activity_type.lower().strip()

    # Common type mappings
    type_mappings: dict[str, str] = {
        "running": "run",
        "run": "run",
        "ride": "ride",
        "bike": "ride",
        "cycling": "ride",
        "virtualride": "ride",
        "ebikeride": "ride",
        "swim": "swim",
        "swimming": "swim",
        "walk": "walk",
        "walking": "walk",
    }

    return type_mappings.get(normalized, normalized)


def _types_match(planned_type: str, activity_type: str | None) -> bool:
    """Check if activity type matches planned type.

    Handles cases where planned_type might be incorrectly set to a workout type
    (easy, long, threshold) instead of a sport type (Run, Bike, Swim).

    Args:
        planned_type: Planned session type (may be sport type or workout type)
        activity_type: Activity type (may be None)

    Returns:
        True if types match
    """
    if not activity_type:
        return False

    planned_normalized = _normalize_activity_type(planned_type)
    activity_normalized = _normalize_activity_type(activity_type)

    if not planned_normalized or not activity_normalized:
        return False

    # Direct match
    if planned_normalized == activity_normalized:
        return True

    # If planned_type is a workout type (easy, long, threshold, etc.) instead of sport type,
    # assume it's a Run and match against Run activities
    # This handles backward compatibility with incorrectly set type fields
    workout_types = {
        "easy", "long", "threshold", "tempo", "interval", "vo2", "fartlek",
        "recovery", "rest", "race", "moderate", "hard", "quality", "hills",
        "strides", "aerobic", "steady", "marathon", "economy", "speed",
    }

    # Workout type likely means it's a Run - allow pairing
    return planned_normalized in workout_types and activity_normalized == "run"


def _get_unpaired_plans(
    *,
    user_id: str,
    activity_date: date,
    activity_type: str | None,
    session: Session,
) -> list[PlannedSession]:
    """Get unpaired planned sessions matching criteria.

    Args:
        user_id: User ID
        activity_date: Activity date
        activity_type: Activity type (for filtering)
        session: Database session

    Returns:
        List of unpaired planned sessions
    """
    # Schema v2: Build query for unpaired plans on the same day (check SessionLink)
    day_start = datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(activity_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    query = (
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= day_start,
            PlannedSession.starts_at <= day_end,
        )
        .order_by(PlannedSession.created_at, PlannedSession.id)
    )

    plans = list(session.scalars(query).all())

    # Schema v2: Filter out plans that already have SessionLink (already paired)
    unpaired_plans = []
    for plan in plans:
        link = get_link_for_planned(session, plan.id)
        if not link:
            unpaired_plans.append(plan)

    plans = unpaired_plans

    # Filter by type match
    return [plan for plan in plans if _types_match(plan.type, activity_type)]


def _get_unpaired_activities(
    *,
    user_id: str,
    planned_date: date,
    planned_type: str,
    session: Session,
) -> list[Activity]:
    """Get unpaired activities matching criteria.

    Args:
        user_id: User ID
        planned_date: Planned session date
        planned_type: Planned session type
        session: Database session

    Returns:
        List of unpaired activities
    """
    # Schema v2: Build query for unpaired activities on the same day (check SessionLink)
    day_start = datetime.combine(planned_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(planned_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    query = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.starts_at >= day_start,
            Activity.starts_at <= day_end,
        )
        .order_by(Activity.created_at, Activity.id)
    )

    activities = list(session.scalars(query).all())

    # Schema v2: Filter out activities that already have SessionLink (already paired)
    unpaired_activities = []
    for activity in activities:
        link = get_link_for_activity(session, activity.id)
        if not link:
            unpaired_activities.append(activity)

    activities = unpaired_activities

    # Filter by type match
    # Use activity.sport directly (schema v2) - activity.type is a property that maps to sport
    return [
        activity for activity in activities if _types_match(planned_type, activity.sport)
    ]


def _log_decision(
    *,
    user_id: str,
    activity: Activity | None,
    planned: PlannedSession | None,
    decision: str,
    reason: str,
    duration_diff_pct: float | None,
    session: Session,
) -> None:
    """Log pairing decision to audit table.

    Args:
        user_id: User ID
        activity: Activity (may be None)
        planned: Planned session (may be None)
        decision: Decision type (paired, rejected, manual_unpair)
        reason: Reason for decision
        duration_diff_pct: Duration difference percentage (nullable)
        session: Database session
    """
    activity_id = activity.id if activity else None
    planned_session_id = planned.id if planned else None

    # Log pairing decision to audit table (non-critical, failures don't break pairing)
    # Use a savepoint to isolate logging failures from the main transaction
    in_transaction = session.in_transaction()
    savepoint = session.begin_nested() if in_transaction else None
    try:
        pairing_decision = PairingDecision(
            user_id=user_id,
            planned_session_id=planned_session_id,
            activity_id=activity_id,
            decision=decision,
            duration_diff_pct=duration_diff_pct,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
        session.add(pairing_decision)
        session.flush()
        if savepoint:
            savepoint.commit()
    except Exception as e:
        # Audit logging is non-critical - rollback savepoint only, not main transaction
        if savepoint:
            savepoint.rollback()
        logger.warning(
            "Failed to log pairing decision to audit table (non-critical)",
            user_id=user_id,
            decision=decision,
            reason=reason,
            error=str(e),
            exc_info=True,
        )


def _pair_from_activity(activity: Activity, session: Session) -> None:
    """Attempt to pair an activity with a planned session.

    Args:
        activity: Activity to pair
        session: Database session
    """
    # Schema v2: Skip if already paired (check SessionLink)
    link = get_link_for_activity(session, activity.id)
    if link:
        logger.debug(
            f"Activity {activity.id} already paired to planned session {link.planned_session_id}",
        )
        return

    # Schema v2: Get activity date from starts_at
    activity_date = activity.starts_at.date() if activity.starts_at else None
    if not activity_date:
        logger.debug(f"Activity {activity.id} has no starts_at, cannot pair")
        return

    # Get candidate planned sessions
    # Use activity.sport directly (schema v2) - activity.type is a property that maps to sport
    plans = _get_unpaired_plans(
        user_id=activity.user_id,
        activity_date=activity_date,
        activity_type=activity.sport,
        session=session,
    )

    if not plans:
        _log_decision(
            user_id=activity.user_id,
            activity=activity,
            planned=None,
            decision="rejected",
            reason="no_candidate",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(
            f"No unpaired planned sessions found for activity {activity.id} on {activity_date}",
        )
        return

    # Calculate duration matches
    if activity.duration_seconds is None:
        _log_decision(
            user_id=activity.user_id,
            activity=activity,
            planned=None,
            decision="rejected",
            reason="no_activity_duration",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(f"Activity {activity.id} has no duration, cannot pair")
        return

    activity_duration_minutes = activity.duration_seconds / 60.0

    matches = []
    for plan in plans:
        if plan.duration_minutes is None:
            continue

        diff_minutes = abs(plan.duration_minutes - activity_duration_minutes)
        diff_pct = diff_minutes / plan.duration_minutes

        if diff_pct <= DURATION_TOLERANCE:
            matches.append((diff_pct, plan))

    if not matches:
        _log_decision(
            user_id=activity.user_id,
            activity=activity,
            planned=None,
            decision="rejected",
            reason="duration_mismatch",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(
            f"No planned sessions within duration tolerance for activity {activity.id}",
        )
        return

    # Sort by: score ASC, created_at ASC, id ASC (deterministic)
    matches.sort(key=lambda x: (x[0], x[1].created_at, x[1].id))
    chosen_plan = matches[0][1]
    chosen_diff_pct = matches[0][0]

    # Persist pairing
    _persist_pairing(chosen_plan, activity, session, chosen_diff_pct)


def _pair_from_planned(planned: PlannedSession, session: Session) -> None:
    """Attempt to pair a planned session with an activity.

    Args:
        planned: Planned session to pair
        session: Database session
    """
    # Schema v2: Skip if already paired (check SessionLink)
    link = get_link_for_planned(session, planned.id)
    if link:
        logger.debug(
            f"Planned session {planned.id} already paired to activity {link.activity_id}",
        )
        return

    # Schema v2: Get planned date from starts_at
    planned_date = planned.starts_at.date() if planned.starts_at else None
    if not planned_date:
        logger.debug(f"Planned session {planned.id} has no starts_at, cannot pair")
        return

    # Get candidate activities
    activities = _get_unpaired_activities(
        user_id=planned.user_id,
        planned_date=planned_date,
        planned_type=planned.type,
        session=session,
    )

    if not activities:
        _log_decision(
            user_id=planned.user_id,
            activity=None,
            planned=planned,
            decision="rejected",
            reason="no_candidate",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(
            f"No unpaired activities found for planned session {planned.id} on {planned_date}",
        )
        return

    # Calculate duration matches
    if planned.duration_minutes is None:
        _log_decision(
            user_id=planned.user_id,
            activity=None,
            planned=planned,
            decision="rejected",
            reason="no_planned_duration",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(f"Planned session {planned.id} has no duration, cannot pair")
        return

    matches = []
    for activity in activities:
        if activity.duration_seconds is None:
            continue

        activity_duration_minutes = activity.duration_seconds / 60.0
        diff_minutes = abs(planned.duration_minutes - activity_duration_minutes)
        diff_pct = diff_minutes / planned.duration_minutes

        if diff_pct <= DURATION_TOLERANCE:
            matches.append((diff_pct, activity))

    if not matches:
        _log_decision(
            user_id=planned.user_id,
            activity=None,
            planned=planned,
            decision="rejected",
            reason="duration_mismatch",
            duration_diff_pct=None,
            session=session,
        )
        logger.debug(
            f"No activities within duration tolerance for planned session {planned.id}",
        )
        return

    # Sort by: score ASC, created_at ASC, id ASC (deterministic)
    matches.sort(key=lambda x: (x[0], x[1].created_at, x[1].id))
    chosen_activity = matches[0][1]
    chosen_diff_pct = matches[0][0]

    # Persist pairing
    _persist_pairing(planned, chosen_activity, session, chosen_diff_pct)


def _persist_pairing(
    planned: PlannedSession,
    activity: Activity,
    session: Session,
    duration_diff_pct: float,
) -> None:
    """Persist pairing relationship (transactional).

    After pairing, this function:
    1. Sets bidirectional pairing links
    2. Gets or creates workout for planned session
    3. Updates activity.workout_id to point to planned workout
    4. Creates WorkoutExecution (triggers compliance calculation)
    5. Performs HR-based reconciliation

    Args:
        planned: Planned session
        activity: Activity
        session: Database session
        duration_diff_pct: Duration difference percentage
    """
    # Schema v2: Create SessionLink with 'proposed' status (auto-pairing creates proposals)
    confidence_score = 1.0 - duration_diff_pct  # Higher confidence = lower diff
    confidence_score = max(0.0, min(1.0, confidence_score))  # Clamp to [0, 1]

    upsert_link(
        session=session,
        user_id=activity.user_id,
        planned_session_id=planned.id,
        activity_id=activity.id,
        status="proposed",  # Auto-pairing creates proposals (can be confirmed later)
        method="auto",
        confidence=confidence_score,
        notes=f"Auto-paired: duration diff {duration_diff_pct:.2%}",
    )

    _log_decision(
        user_id=activity.user_id,
        activity=activity,
        planned=planned,
        decision="paired",
        reason="auto_duration_match",
        duration_diff_pct=duration_diff_pct,
        session=session,
    )

    logger.info(
        f"Auto-paired planned session {planned.id} with activity {activity.id} "
        f"(duration diff: {duration_diff_pct:.2%})",
    )

    # Ensure workout exists for planned session
    try:
        workout = WorkoutFactory.get_or_create_for_planned_session(session, planned)
        logger.debug(
            f"Workout ensured for planned session {planned.id}",
            workout_id=workout.id,
        )
    except Exception as e:
        logger.warning(
            f"Failed to get/create workout for planned session {planned.id}: {e}",
        )
        # Continue even if workout creation fails - pairing still succeeds
        workout = None

    # Note: activity.workout_id does not exist in schema v2
    # Relationships go through session_links table (planned_sessions <-> session_links <-> activities)
    if workout:
        # Create WorkoutExecution (triggers compliance calculation)
        try:
            WorkoutFactory.attach_activity(session, workout, activity)
            logger.debug(
                f"Created execution and compliance for workout {workout.id}",
            )
        except Exception as e:
            logger.warning(
                f"Failed to create execution/compliance for workout {workout.id}: {e}",
            )
            # Continue even if execution/compliance creation fails

    # Perform HR-based reconciliation (passive, read-only)
    try:
        reconcile_activity_if_paired(session, activity)
    except Exception as e:
        logger.warning(f"Reconciliation failed after pairing {activity.id} with {planned.id}: {e}")


def try_auto_pair(
    *,
    activity: Activity | None = None,
    planned: PlannedSession | None = None,
    session: Session,
) -> None:
    """Attempt automatic pairing (order-independent entry point).

    Args:
        activity: Activity to pair (optional)
        planned: Planned session to pair (optional)
        session: Database session

    Raises:
        ValueError: If neither activity nor planned is provided
    """
    if not activity and not planned:
        raise ValueError("Either activity or planned must be provided")

    if activity:
        _pair_from_activity(activity, session)
    else:
        if not planned:
            raise ValueError("Either activity or planned must be provided")
        _pair_from_planned(planned, session)
