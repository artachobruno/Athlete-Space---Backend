"""Calendar API endpoints with real activity data.

Step 6: Replaces mock data with real activities from database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, quoted_name, select, text
from sqlalchemy.exc import InternalError, ProgrammingError
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.schemas import (
    CalendarSeasonResponse,
    CalendarSession,
    CalendarSessionsResponse,
    CalendarTodayResponse,
    CalendarWeekResponse,
)
from app.calendar.auto_match_service import auto_match_sessions
from app.calendar.reconciliation_service import reconcile_calendar
from app.calendar.view_helper import calendar_session_from_view_row, get_calendar_items_from_view
from app.db.models import Activity, CoachFeedback, PlannedSession, StravaAccount, User
from app.db.session import get_session
from app.pairing.session_links import (
    get_link_for_activity,
    get_link_for_planned,
    unlink_by_planned,
    upsert_link,
)
from app.utils.timezone import now_user, to_utc
from app.workouts.execution_models import WorkoutExecution
from app.workouts.llm.today_session_generator import generate_today_session_content
from app.workouts.models import Workout, WorkoutStep
from app.workouts.step_utils import infer_step_name
from app.workouts.targets_utils import get_distance_meters, get_duration_seconds, get_target_metric

router = APIRouter(prefix="/calendar", tags=["calendar"])

# Schema v2: Use calendar_items view for unified querying
SQL_CALENDAR_ITEMS = text("""
SELECT kind, starts_at, ends_at, sport, title, status, payload
FROM calendar_items
WHERE user_id = :user_id
  AND starts_at >= :start
  AND starts_at < :end
ORDER BY starts_at ASC
""")


def _raise_user_not_found() -> None:
    """Raise HTTPException for user not found."""
    raise HTTPException(status_code=404, detail="User not found")


def _get_athlete_id(session: Session, user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        Athlete ID as integer, or None if not found
    """
    account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if account:
        try:
            return int(account[0].athlete_id)
        except (ValueError, TypeError):
            return None
    return None


def _get_planned_sessions_safe(
    session: Session,
    user_id: str,
    start_date: datetime,
    end_date: datetime,
) -> list[PlannedSession]:
    """Get planned sessions with safe error handling.

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date
        end_date: End date

    Returns:
        List of planned sessions, empty list on schema errors
    """
    try:
        # Schema v2: use starts_at instead of date
        planned_sessions = (
            session.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.starts_at >= start_date,
                    PlannedSession.starts_at <= end_date,
                    # NULL-safe status filter: exclude only explicitly excluded statuses
                    # NULL statuses and "planned" statuses are included
                    func.coalesce(PlannedSession.status, "planned").notin_(["completed", "deleted", "skipped"]),
                )
                .order_by(PlannedSession.starts_at)
            )
            .scalars()
            .all()
        )
        return list(planned_sessions)
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[CALENDAR] Database schema issue querying planned sessions. Missing column. Returning empty: {e!r}")
            # Rollback the transaction to prevent "InFailedSqlTransaction" errors on subsequent queries
            session.rollback()
            return []
        raise


def _get_activities_safe(
    session: Session,
    user_id: str,
    start_date: datetime,
    end_date: datetime,
    matched_activity_ids: set[str],
) -> list[CalendarSession]:  # CalendarSession is Pydantic schema from schemas.py
    """Get activities with safe error handling.

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date
        end_date: End date
        matched_activity_ids: Set of activity IDs already matched to planned sessions

    Returns:
        List of activity sessions, empty list on schema errors
    """
    try:
        # Schema v2: use starts_at instead of start_time
        activities = (
            session.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.starts_at >= start_date,
                    Activity.starts_at <= end_date,
                )
                .order_by(Activity.starts_at)
            )
            .scalars()
            .all()
        )
        return [_activity_to_session(a) for a in activities if a.id not in matched_activity_ids]
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[CALENDAR] Database schema issue querying activities. Missing column. Returning empty: {e!r}")
            # Rollback the transaction to prevent "InFailedSqlTransaction" errors on subsequent queries
            session.rollback()
            return []
        raise


def _run_reconciliation_safe(
    user_id: str,
    athlete_id: int,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, str], set[str]]:
    """Run reconciliation with safe error handling and auto-matching.

    This function:
    1. Runs reconciliation to find matches
    2. Automatically creates workouts for matches (idempotent)
    3. Returns reconciliation status map

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Start date
        end_date: End date

    Returns:
        Tuple of (reconciliation_map, matched_activity_ids)
    """
    reconciliation_map: dict[str, str] = {}
    matched_activity_ids: set[str] = set()

    try:
        reconciliation_results = reconcile_calendar(
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            end_date=end_date,
        )

        # Auto-match: create workouts for matches
        try:
            auto_match_sessions(user_id=user_id, reconciliation_results=reconciliation_results)
        except Exception as e:
            logger.warning(f"[CALENDAR] Auto-match failed, continuing with reconciliation: {e!r}")

        # Build reconciliation map
        # CRITICAL: Only override DB status if there's a REAL matched activity
        # MISSED status should NOT flip planned → completed (keep it as planned)
        # BUT we DO include MISSED in reconciliation_map so frontend can display MISSED label
        for result in reconciliation_results:
            # Only set reconciliation status if:
            # 1. There's a matched activity (COMPLETED, PARTIAL, SUBSTITUTED have matched_activity_id)
            # 2. OR session is explicitly SKIPPED (user marked it)
            # 3. OR session is MISSED (for frontend label display, but doesn't change DB status)
            if result.matched_activity_id or result.status.value in {"skipped", "missed"}:
                reconciliation_map[result.session_id] = result.status.value
            else:
                # Other statuses (shouldn't happen, but be safe)
                reconciliation_map[result.session_id] = result.status.value

            if result.matched_activity_id:
                matched_activity_ids.add(result.matched_activity_id)
    except Exception as e:
        logger.warning(f"[CALENDAR] Reconciliation failed, using planned status: {e!r}")

    return reconciliation_map, matched_activity_ids


def _planned_session_to_calendar(
    planned: PlannedSession,
    reconciliation_status: str | None = None,
) -> CalendarSession:
    """Convert PlannedSession to CalendarSession.

    Args:
        planned: PlannedSession record
        reconciliation_status: Optional status from reconciliation (overrides planned.status)

    Returns:
        CalendarSession object
    """
    time_str = planned.time if planned.time else None

    # Use reconciliation status if provided, otherwise use planned status
    # Map PARTIAL and SUBSTITUTED to "completed" for UI display (they have matched activities)
    # Preserve "missed" in status field for frontend to detect and display MISSED label
    status = reconciliation_status if reconciliation_status else planned.status
    if status in {"partial", "substituted"}:
        # These statuses indicate a matched activity, so treat as completed for UI
        status = "completed"
    # Note: "missed" status is preserved here to allow frontend to display MISSED label
    # even though the card kind will remain "planned"

    # Capitalize first letter of session type
    session_type: str = planned.type.capitalize()

    # Convert completed_at to ISO 8601 string (handles timezone-aware datetimes properly)
    # Use getattr to handle case where column doesn't exist in database (migration pending)
    completed_at = getattr(planned, "completed_at", None)
    completed_at_str = completed_at.isoformat() if completed_at else None

    # Schema v2: use starts_at, duration_seconds, distance_meters
    # CalendarSession schema may still expect old names in response (compatibility)
    # Normalize execution_notes: trim whitespace, empty string → None
    execution_notes = planned.execution_notes
    if execution_notes:
        execution_notes = execution_notes.strip()
        if not execution_notes:
            execution_notes = None

    return CalendarSession(
        id=str(planned.id),  # Convert UUID to string
        date=planned.starts_at.strftime("%Y-%m-%d") if planned.starts_at else "",
        time=time_str,
        type=session_type,
        title=planned.title or "",
        duration_minutes=planned.duration_seconds // 60 if planned.duration_seconds else None,  # Convert seconds to minutes for response
        distance_km=round(planned.distance_meters / 1000.0, 2) if planned.distance_meters else None,  # Convert meters to km
        intensity=planned.intensity,
        status=status,
        notes=planned.notes,
        execution_notes=execution_notes,
        workout_id=str(planned.workout_id) if planned.workout_id else None,  # Convert UUID to string if present
        completed_activity_id=None,  # Schema v2: removed, use session_links
        completed=status == "completed",
        completed_at=completed_at_str,
        coach_insight=None,  # Not stored in PlannedSession - generated dynamically for today's sessions only
    )


def _activity_to_session(activity: Activity) -> CalendarSession:
    """Convert Activity to CalendarSession.

    Args:
        activity: Activity record

    Returns:
        CalendarSession object
    """
    # Determine intensity based on duration
    if activity.duration_seconds is None:
        duration_hours = 0.0
        duration_minutes = 0
    else:
        duration_hours = activity.duration_seconds / 3600.0
        duration_minutes = int(activity.duration_seconds / 60)

    if duration_hours > 1.5:
        intensity = "easy"
    elif duration_hours > 0.75:
        intensity = "moderate"
    else:
        intensity = "hard"

    # Schema v2: use starts_at and sport instead of start_time and type
    # Format time
    time_str = activity.starts_at.strftime("%H:%M") if activity.starts_at else ""

    # Determine distance in km
    if activity.distance_meters is not None and activity.distance_meters > 0:
        distance_km = round(activity.distance_meters / 1000.0, 2)
    else:
        distance_km = None

    activity_type = activity.sport or "Activity"  # Schema v2: sport instead of type

    return CalendarSession(
        id=activity.id,
        date=activity.starts_at.strftime("%Y-%m-%d") if activity.starts_at else "",  # Schema v2: starts_at
        time=time_str,
        type=activity_type,
        title=f"{activity_type} - {duration_minutes}min",
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        intensity=intensity,
        status="completed",  # All activities from Strava are completed
        notes=None,
        execution_notes=None,  # Activities don't have execution notes
        workout_id=None,  # Schema v2: activity.workout_id does not exist - relationships through session_links/executions
    )


@router.get("/season", response_model=CalendarSeasonResponse)
def get_season(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for the current season from real activities.

    Uses reconciliation to determine authoritative session status.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSeasonResponse with all sessions in the season
    """
    logger.info(f"[CALENDAR] GET /calendar/season called for user_id={user_id}")
    with get_session() as session:
        # Get user for timezone
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            raise HTTPException(status_code=404, detail="User not found")
        user = user_result[0]

        # Get current time in user's timezone
        now_local = now_user(user)

        # Calculate season boundaries in user's timezone
        season_start_local = now_local - timedelta(days=90)
        season_end_local = now_local + timedelta(days=90)

        # Convert to UTC for database queries
        season_start = to_utc(season_start_local)
        season_end = to_utc(season_end_local)
        start_date = season_start_local.date()
        end_date = season_end_local.date()

        logger.info(
            f"[CALENDAR] user={user_id} tz={user.timezone} season={start_date}-{end_date}"
        )

        # Schema v2: Query calendar_items view directly (single unified query)
        view_rows = get_calendar_items_from_view(session, user_id, season_start, season_end)
        logger.info(
            "[calendar_view] endpoint=/season user=%s start=%s end=%s rows=%s",
            user_id,
            season_start.isoformat() if season_start else None,
            season_end.isoformat() if season_end else None,
            len(view_rows),
        )

        # Build pairing maps for efficient lookup
        pairing_map: dict[str, str] = {}  # planned_session_id -> activity_id
        activity_pairing_map: dict[str, str] = {}  # activity_id -> planned_session_id

        for row in view_rows:
            item_id = str(row.get("item_id", ""))
            kind = str(row.get("kind", ""))

            if kind == "planned":
                link = get_link_for_planned(session, item_id)
                if link:
                    pairing_map[item_id] = link.activity_id
            elif kind == "activity":
                link = get_link_for_activity(session, item_id)
                if link:
                    activity_pairing_map[item_id] = link.planned_session_id

        # Enrich view rows with pairing info before converting to CalendarSession
        # Filter out activities that are paired to a planned session (show only the planned session card)
        enriched_rows = []
        for row in view_rows:
            item_id = str(row.get("item_id", ""))
            kind = str(row.get("kind", ""))
            payload = row.get("payload") or {}

            # Skip activities that are paired to a planned session (they'll be shown via the planned session card)
            if kind == "activity" and item_id in activity_pairing_map:
                continue

            # Add pairing info to payload
            if kind == "planned" and item_id in pairing_map:
                payload = {**payload, "paired_activity_id": pairing_map[item_id]}

            enriched_row = {**row, "payload": payload}
            enriched_rows.append(enriched_row)

        all_sessions = [calendar_session_from_view_row(row) for row in enriched_rows]

        # Sort by date
        all_sessions.sort(key=lambda s: s.date)

        # Compute stats from enriched rows
        # Count by kind and status
        db_planned = sum(1 for row in enriched_rows if row.get("kind") == "planned" and row.get("status") == "planned")
        db_completed = sum(1 for row in enriched_rows if row.get("kind") == "planned" and row.get("status") == "completed")

        # Count activities (all are "completed")
        activity_count = sum(1 for row in enriched_rows if row.get("kind") == "activity")

        # Final counts (activities always count as completed)
        planned_sessions_from_view = [s for s in all_sessions if s.status == "planned"]
        final_planned = len(planned_sessions_from_view)
        final_completed = activity_count + sum(1 for s in all_sessions if s.status == "completed")

    return CalendarSeasonResponse(
        season_start=season_start_local.strftime("%Y-%m-%d"),
        season_end=season_end_local.strftime("%Y-%m-%d"),
        sessions=all_sessions,
        total_sessions=len(all_sessions),
        completed_sessions=final_completed,
        planned_sessions=final_planned,
        # Expose DB vs final status for debugging
        completed_sessions_db=db_completed,
        planned_sessions_db=db_planned,
        completed_sessions_final=final_completed,
        planned_sessions_final=final_planned,
    )


@router.get("/week", response_model=CalendarWeekResponse)
def get_week(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for the current week from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarWeekResponse with sessions for this week
    """
    logger.info(f"[CALENDAR] GET /calendar/week called for user_id={user_id}")
    try:
        with get_session() as session:
            # Get user for timezone
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found()
            user = user_result[0]

            # Get current time in user's timezone
            now_local = now_user(user)

            # Get Monday of current week in user's timezone
            days_since_monday = now_local.weekday()
            monday_local = (now_local - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            sunday_local = monday_local + timedelta(days=6, hours=23, minutes=59, seconds=59)

            # Convert to UTC for database queries
            monday = to_utc(monday_local)
            sunday = to_utc(sunday_local)

            logger.info(
                f"[CALENDAR] user={user_id} tz={user.timezone} week={monday_local.date()}-{sunday_local.date()}"
            )

            # Schema v2: Query calendar_items view directly (single unified query)
            view_rows = get_calendar_items_from_view(session, user_id, monday, sunday)
            logger.info(
                "[calendar_view] endpoint=/week user=%s start=%s end=%s rows=%s",
                user_id,
                monday.isoformat() if monday else None,
                sunday.isoformat() if sunday else None,
                len(view_rows),
            )

            # Build pairing maps for efficient lookup
            pairing_map: dict[str, str] = {}  # planned_session_id -> activity_id
            activity_pairing_map: dict[str, str] = {}  # activity_id -> planned_session_id

            for row in view_rows:
                item_id = str(row.get("item_id", ""))
                kind = str(row.get("kind", ""))

                if kind == "planned":
                    link = get_link_for_planned(session, item_id)
                    if link:
                        pairing_map[item_id] = link.activity_id
                elif kind == "activity":
                    link = get_link_for_activity(session, item_id)
                    if link:
                        activity_pairing_map[item_id] = link.planned_session_id

            # Enrich view rows with pairing info before converting to CalendarSession
            # Filter out activities that are paired to a planned session (show only the planned session card)
            enriched_rows = []
            for row in view_rows:
                item_id = str(row.get("item_id", ""))
                kind = str(row.get("kind", ""))
                payload = row.get("payload") or {}

                # Skip activities that are paired to a planned session (they'll be shown via the planned session card)
                if kind == "activity" and item_id in activity_pairing_map:
                    continue

                # Add pairing info to payload
                if kind == "planned" and item_id in pairing_map:
                    payload = {**payload, "paired_activity_id": pairing_map[item_id]}

                enriched_row = {**row, "payload": payload}
                enriched_rows.append(enriched_row)

            sessions = [calendar_session_from_view_row(row) for row in enriched_rows]

            # Sort by date and time
            sessions.sort(key=lambda s: (s.date, s.time or ""))

        return CalendarWeekResponse(
            week_start=monday_local.strftime("%Y-%m-%d"),
            week_end=sunday_local.strftime("%Y-%m-%d"),
            sessions=sessions,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"[CALENDAR] Database schema error in /calendar/week. Missing column. Returning empty week: {e!r}"
            )
            # Return empty week instead of 500 - migrations will fix this
            # Calculate fallback dates using UTC (can't access user timezone in error handler)
            today_utc = datetime.now(timezone.utc).date()
            days_since_monday = today_utc.weekday()
            monday_fallback = today_utc - timedelta(days=days_since_monday)
            sunday_fallback = monday_fallback + timedelta(days=6)
            return CalendarWeekResponse(
                week_start=monday_fallback.strftime("%Y-%m-%d"),
                week_end=sunday_fallback.strftime("%Y-%m-%d"),
                sessions=[],
            )
        logger.exception(f"[CALENDAR] Error in /calendar/week: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get calendar week: {e!s}") from e


def _convert_workout_step_to_schema(
    db_step: WorkoutStep, workout_obj: Workout | None
) -> dict[str, Any]:
    """Convert WorkoutStep database model to WorkoutStepSchema format.

    Args:
        db_step: WorkoutStep database model
        workout_obj: Optional Workout object for raw_notes context

    Returns:
        Dictionary matching WorkoutStepSchema format
    """
    targets = db_step.targets or {}

    # Extract duration and distance using helper functions
    duration_seconds = get_duration_seconds(targets)
    duration_min = duration_seconds // 60 if duration_seconds else None
    distance_meters = get_distance_meters(targets)
    distance_km = round(distance_meters / 1000.0, 2) if distance_meters else None

    # Extract intensity from target metric or infer from step_type
    intensity_str = get_target_metric(targets)
    if not intensity_str and db_step.step_type:
        # Map step_type to intensity if no target metric
        step_type_lower = db_step.step_type.lower()
        if "interval" in step_type_lower or "vo2" in step_type_lower:
            intensity_str = "vo2"
        elif "threshold" in step_type_lower or "tempo" in step_type_lower:
            intensity_str = "threshold"
        elif "steady" in step_type_lower:
            intensity_str = "steady"
        elif (
            "easy" in step_type_lower
            or "recovery" in step_type_lower
            or "warmup" in step_type_lower
            or "cooldown" in step_type_lower
        ):
            intensity_str = "easy"

    # Use purpose, inferred name, or step_type as name
    step_name = (
        db_step.purpose
        or (infer_step_name(db_step, workout_obj.raw_notes if workout_obj else None))
        or db_step.step_type
        or f"Step {db_step.step_index + 1}"
    )

    return {
        "order": db_step.step_index + 1,  # Convert 0-indexed to 1-indexed
        "name": step_name,
        "duration_min": duration_min,
        "distance_km": distance_km,
        "intensity": intensity_str,
        "notes": db_step.instructions,
    }


def _persist_coach_feedback(
    session: Session,
    planned_session_id: str,
    user_id: str,
    instructions: list[str],
    steps: list[dict[str, Any]],
    coach_insight: str,
) -> None:
    """Persist coach feedback to database.

    Args:
        session: Database session
        planned_session_id: ID of the planned session
        user_id: User ID
        instructions: List of execution instructions
        steps: List of workout step dictionaries
        coach_insight: Coach insight text
    """
    try:
        # Verify planned_session_id exists in planned_sessions table
        planned_session_exists = session.execute(
            select(PlannedSession.id).where(PlannedSession.id == planned_session_id)
        ).scalar_one_or_none()

        if not planned_session_exists:
            logger.warning(
                "[COACH_FEEDBACK] Planned session not found, cannot persist feedback",
                planned_session_id=planned_session_id,
                user_id=user_id,
            )
            return

        # Check if feedback already exists
        existing = session.execute(
            select(CoachFeedback).where(CoachFeedback.planned_session_id == planned_session_id)
        ).scalar_one_or_none()

        if existing:
            # Update existing feedback
            existing.instructions = instructions
            existing.steps = steps
            existing.coach_insight = coach_insight
            existing.updated_at = datetime.now(timezone.utc)
            logger.info(
                "[COACH_FEEDBACK] Updating existing feedback",
                planned_session_id=planned_session_id,
                instructions_count=len(instructions),
                steps_count=len(steps),
                has_insight=bool(coach_insight),
            )
        else:
            # Create new feedback
            feedback = CoachFeedback(
                planned_session_id=planned_session_id,
                user_id=user_id,
                instructions=instructions,
                steps=steps,
                coach_insight=coach_insight,
            )
            session.add(feedback)
            logger.info(
                "[COACH_FEEDBACK] Adding new feedback",
                planned_session_id=planned_session_id,
                user_id=user_id,
                instructions_count=len(instructions),
                steps_count=len(steps),
                has_insight=bool(coach_insight),
            )

        # Flush to immediately expose any constraint violations
        session.flush()
        logger.info("[COACH_FEEDBACK] Flush succeeded")

        session.commit()
        logger.info(
            "[COACH_FEEDBACK] Successfully persisted feedback",
            planned_session_id=planned_session_id,
        )
    except Exception as e:
        logger.exception(
            "[COACH_FEEDBACK] Failed to persist coach feedback",
            planned_session_id=planned_session_id,
            user_id=user_id,
            error_type=type(e).__name__,
            error=str(e),
        )
        session.rollback()
        # Don't raise - allow request to continue without persisted feedback


async def _process_planned_session_for_today(
    session: Session, row: dict[str, Any], user_id: str
) -> tuple[list[str] | None, list[dict[str, Any]] | None, str | None]:
    """Process a planned session row and generate LLM content if needed.

    Args:
        session: Database session
        row: Calendar item row from view
        user_id: User ID for the session

    Returns:
        Tuple of (instructions, steps, coach_insight)
    """
    try:
        # Extract session details
        payload: dict[str, Any] = row.get("payload") or {}
        session_title = row.get("title") or payload.get("title") or "Training Session"
        session_type = payload.get("session_type")
        duration_seconds = payload.get("duration_seconds")
        duration_minutes = int(duration_seconds // 60) if duration_seconds else None
        distance_meters = payload.get("distance_meters")
        distance_km = round(float(distance_meters) / 1000.0, 2) if distance_meters else None
        intensity = payload.get("intensity")
        notes = payload.get("notes")
        intent = payload.get("intent", "").lower()
        is_rest_day = intent == "rest" or "rest" in (session_title or "").lower()
        workout_id = payload.get("workout_id")

        # ❗ SINGLE SOURCE OF TRUTH: Use canonical workout steps from database
        # Only generate LLM steps if no workout_id or no steps exist
        instructions: list[str] | None = None
        steps: list[dict[str, Any]] | None = None
        coach_insight: str | None = None

        if workout_id:
            # ❗ SINGLE SOURCE OF TRUTH: Fetch actual workout steps from database
            # Use canonical workout steps, not LLM-generated generic "Warm-up, Main, Cooldown"
            canonical_steps = _load_canonical_workout_steps(session, workout_id)
            if canonical_steps:
                steps = canonical_steps
                logger.info(
                    "Using canonical workout steps",
                    session_id=row.get("item_id"),
                    workout_id=workout_id,
                    steps_count=len(steps),
                )
            else:
                # No steps found, fall back to LLM generation
                workout_id = None

        # Only generate LLM content if no workout_id or no steps found
        if not workout_id or not steps:
            # Generate LLM content (fallback for sessions without structured workout)
            content = await generate_today_session_content(
                session_title=session_title,
                session_type=session_type,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                intensity=intensity,
                notes=notes,
                is_rest_day=is_rest_day,
            )

            instructions = content.instructions
            if not steps:  # Only use LLM steps if we didn't get canonical steps
                steps = [
                    {
                        "order": step.order,
                        "name": step.name,
                        "duration_min": step.duration_min,
                        "distance_km": step.distance_km,
                        "intensity": step.intensity,
                        "notes": step.notes,
                    }
                    for step in content.steps
                ]
            coach_insight = content.coach_insight

            # Persist coach feedback to database
            item_id = row.get("item_id")
            if item_id:
                _persist_coach_feedback(
                    session,
                    planned_session_id=str(item_id),
                    user_id=user_id,
                    instructions=instructions or [],
                    steps=steps or [],
                    coach_insight=coach_insight or "",
                )

            logger.info(
                "Generated LLM content for today session",
                session_id=row.get("item_id"),
                instructions_count=len(instructions) if instructions else 0,
                steps_count=len(steps) if steps else 0,
                used_canonical_steps=bool(workout_id and steps),
            )
        else:
            # We have canonical steps, but still generate instructions and coach_insight
            # (steps are already set from database)
            content = await generate_today_session_content(
                session_title=session_title,
                session_type=session_type,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                intensity=intensity,
                notes=notes,
                is_rest_day=is_rest_day,
            )
            instructions = content.instructions
            coach_insight = content.coach_insight

            # Persist coach feedback to database
            item_id = row.get("item_id")
            if item_id:
                _persist_coach_feedback(
                    session,
                    planned_session_id=str(item_id),
                    user_id=user_id,
                    instructions=instructions or [],
                    steps=steps or [],
                    coach_insight=coach_insight or "",
                )
    except Exception as e:
        # Log error but don't fail the request - return session without LLM content
        logger.warning(
            "Failed to generate LLM content for today session",
            session_id=row.get("item_id"),
            error=str(e),
        )
        return None, None, None
    else:
        return instructions, steps, coach_insight


def _load_canonical_workout_steps(
    session: Session, workout_id: str
) -> list[dict[str, Any]] | None:
    """Load canonical workout steps from database.

    Args:
        session: Database session
        workout_id: Workout ID

    Returns:
        List of step dictionaries or None if not found
    """
    try:
        # Fetch workout for raw_notes (needed for step name inference)
        workout_obj = session.execute(
            select(Workout).where(Workout.id == workout_id)
        ).scalar_one_or_none()

        workout_steps_query = (
            session.execute(
                select(WorkoutStep)
                .where(WorkoutStep.workout_id == workout_id)
                .order_by(WorkoutStep.step_index.asc())
            )
            .scalars()
            .all()
        )

        if not workout_steps_query:
            return None

        # Convert WorkoutStep to WorkoutStepSchema format
        steps = [
            _convert_workout_step_to_schema(db_step, workout_obj)
            for db_step in workout_steps_query
        ]

        logger.info(
            "Loaded canonical workout steps from database",
            workout_id=workout_id,
            steps_count=len(steps),
        )
    except Exception as e:
        logger.warning(
            "Failed to load workout steps from database",
            workout_id=workout_id,
            error=str(e),
        )
        return None
    else:
        return steps


@router.get("/today", response_model=CalendarTodayResponse)
async def get_today(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for today from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarTodayResponse with sessions for today
    """
    logger.info(f"[CALENDAR] GET /calendar/today called for user_id={user_id}")
    try:
        with get_session() as session:
            # Get user for timezone
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found()
            user = user_result[0]

            # Get current time in user's timezone
            now_local = now_user(user)

            # Get today boundaries in user's timezone
            today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end_local = today_local + timedelta(days=1) - timedelta(microseconds=1)

            # Convert to UTC for database queries
            today = to_utc(today_local)
            today_end = to_utc(today_end_local)
            today_str = today_local.strftime("%Y-%m-%d")

            logger.info(f"[CALENDAR] user={user_id} tz={user.timezone} today={today_local.date()}")

            # Schema v2: Query calendar_items view directly (single unified query)
            view_rows = get_calendar_items_from_view(session, user_id, today, today_end)
            logger.info(
                "[calendar_view] endpoint=/today user=%s start=%s end=%s rows=%s",
                user_id,
                today.isoformat() if today else None,
                today_end.isoformat() if today_end else None,
                len(view_rows),
            )

            # Build pairing maps for efficient lookup
            pairing_map: dict[str, str] = {}  # planned_session_id -> activity_id
            activity_pairing_map: dict[str, str] = {}  # activity_id -> planned_session_id

            for row in view_rows:
                item_id = str(row.get("item_id", ""))
                kind = str(row.get("kind", ""))

                if kind == "planned":
                    link = get_link_for_planned(session, item_id)
                    if link:
                        pairing_map[item_id] = link.activity_id
                elif kind == "activity":
                    link = get_link_for_activity(session, item_id)
                    if link:
                        activity_pairing_map[item_id] = link.planned_session_id

            # Enrich view rows with pairing info before processing
            # Filter out activities that are paired to a planned session (show only the planned session card)
            enriched_rows = []
            for row in view_rows:
                item_id = str(row.get("item_id", ""))
                kind = str(row.get("kind", ""))
                payload = row.get("payload") or {}

                # Skip activities that are paired to a planned session (they'll be shown via the planned session card)
                if kind == "activity" and item_id in activity_pairing_map:
                    continue

                # Add pairing info to payload
                if kind == "planned" and item_id in pairing_map:
                    payload = {**payload, "paired_activity_id": pairing_map[item_id]}

                enriched_row = {**row, "payload": payload}
                enriched_rows.append(enriched_row)

            # Generate LLM content for planned sessions (not completed ones) if not already persisted
            sessions = []
            for row in enriched_rows:
                # Only generate LLM content for planned sessions (not activities or completed)
                # and only if feedback is not already in the view
                kind = str(row.get("kind", ""))
                status = str(row.get("status", "planned"))
                is_planned = kind == "planned" and status == "planned"
                payload = row.get("payload") or {}
                has_persisted_feedback = bool(payload.get("coach_insight") or payload.get("instructions"))

                instructions: list[str] | None = None
                steps: list[dict[str, Any]] | None = None
                coach_insight: str | None = None

                # Only generate if it's a planned session and feedback is not already persisted
                if is_planned and not has_persisted_feedback:
                    instructions, steps, coach_insight = await _process_planned_session_for_today(
                        session, row, user_id
                    )

                sessions.append(
                    calendar_session_from_view_row(
                        row,
                        instructions=instructions,
                        steps=steps,
                        coach_insight=coach_insight,
                        prefer_view_data=True,  # Prefer persisted data from view
                    )
                )

            # Sort by time
            sessions.sort(key=lambda s: s.time or "23:59")

        return CalendarTodayResponse(
            date=today_str,
            sessions=sessions,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"[CALENDAR] Database schema error in /calendar/today. Missing column. Returning empty day: {e!r}"
            )
            # Return empty day instead of 500 - migrations will fix this
            # Calculate fallback date using UTC (can't access user timezone in error handler)
            today_fallback = datetime.now(timezone.utc).date()
            today_str_fallback = today_fallback.strftime("%Y-%m-%d")
            return CalendarTodayResponse(
                date=today_str_fallback,
                sessions=[],
            )
        logger.exception(f"[CALENDAR] Error in /calendar/today: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get calendar today: {e!s}") from e


@router.get("/sessions", response_model=CalendarSessionsResponse)
def get_sessions(limit: int = 50, offset: int = 0, user_id: str = Depends(get_current_user_id)):
    """Get list of calendar sessions from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        limit: Maximum number of sessions to return (default: 50)
        offset: Number of sessions to skip (default: 0)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSessionsResponse with list of sessions
    """
    logger.info(f"[CALENDAR] GET /calendar/sessions called for user_id={user_id}: limit={limit}, offset={offset}")

    with get_session() as session:
        # Get athlete_id for reconciliation
        athlete_id = _get_athlete_id(session, user_id)

        # Get planned sessions
        planned_sessions = (
            # Schema v2: use starts_at instead of date
            session.execute(select(PlannedSession).where(PlannedSession.user_id == user_id).order_by(PlannedSession.starts_at.desc()))
            .scalars()
            .all()
        )
        planned_list = list(planned_sessions)

        # Run reconciliation if we have athlete_id and planned sessions
        if athlete_id and planned_list:
            min_date = min(p.date.date() if isinstance(p.date, datetime) else p.date for p in planned_list)
            max_date = max(p.date.date() if isinstance(p.date, datetime) else p.date for p in planned_list)
            reconciliation_map, matched_activity_ids = _run_reconciliation_safe(user_id, athlete_id, min_date, max_date)
        else:
            reconciliation_map, matched_activity_ids = {}, set()

        # Get activities (optimized: uses composite index on user_id + starts_at) (schema v2)
        activities = (
            session.execute(select(Activity).where(Activity.user_id == user_id).order_by(Activity.starts_at.desc())).scalars().all()
        )
        # Filter out activities that are matched to planned sessions
        activity_sessions = [_activity_to_session(a) for a in activities if a.id not in matched_activity_ids]

        # Convert planned sessions with reconciliation status
        planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

        # Combine and sort by date (most recent first)
        all_sessions = activity_sessions + planned_calendar_sessions
        all_sessions.sort(key=lambda s: s.date, reverse=True)

        total = len(all_sessions)
        sessions = all_sessions[offset : offset + limit]

    return CalendarSessionsResponse(
        sessions=sessions,
        total=total,
    )


@router.get("/sessions/{session_id}", response_model=CalendarSession)
def get_session_by_id(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get a single planned session by ID.

    This endpoint returns the full details of a planned session, including
    reconciliation status if available. This allows the frontend to display
    session details when clicked, similar to how activities are displayed.

    Args:
        session_id: ID of the planned session to retrieve
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSession with full session details

    Raises:
        HTTPException: If session not found or doesn't belong to user
    """
    logger.info(f"[CALENDAR] GET /calendar/sessions/{session_id} called for user_id={user_id}")

    with get_session() as db_session:
        # Find the planned session
        planned_session = db_session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            raise HTTPException(status_code=404, detail="Planned session not found")

        # Get athlete_id for reconciliation
        athlete_id = _get_athlete_id(db_session, user_id)

        # Run reconciliation if we have athlete_id
        reconciliation_status: str | None = None
        if athlete_id:
            try:
                # Get the date range for reconciliation (just the session date)
                session_date = planned_session.date.date() if isinstance(planned_session.date, datetime) else planned_session.date
                reconciliation_results = reconcile_calendar(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    start_date=session_date,
                    end_date=session_date,
                )
                # Find the reconciliation result for this session
                for result in reconciliation_results:
                    if result.session_id == session_id:
                        reconciliation_status = result.status.value
                        break
            except Exception as e:
                logger.warning(f"[CALENDAR] Reconciliation failed for session {session_id}: {e!r}")

        return _planned_session_to_calendar(planned_session, reconciliation_status)


class UpdateSessionStatusRequest(BaseModel):
    """Request to update a planned session's status."""

    status: str = Field(..., description="New status: planned | completed | skipped | deleted")
    completed_activity_id: str | None = Field(
        default=None,
        description="ID of the completed activity if status is 'completed'",
    )


def _delete_workout_if_orphaned(
    session: Session,
    workout_id: str,
    session_id: str,
    user_id: str,
) -> None:
    """Delete workout and its steps if not referenced elsewhere."""
    try:
        # Check if workout is referenced by any activities
        activity_count = session.execute(
            select(func.count(WorkoutExecution.id)).where(WorkoutExecution.workout_id == workout_id)
        ).scalar() or 0

        # Check if workout is referenced by any other planned sessions
        other_planned_count = session.execute(
            select(func.count(PlannedSession.id)).where(
                PlannedSession.workout_id == workout_id,
                PlannedSession.id != session_id,
            )
        ).scalar() or 0

        # Only delete workout if it's not referenced elsewhere
        if activity_count == 0 and other_planned_count == 0:
            # Delete workout_steps first (cascade order)
            workout_steps = session.execute(
                select(WorkoutStep).where(WorkoutStep.workout_id == workout_id)
            ).scalars().all()
            for step in workout_steps:
                session.delete(step)

            # Delete workout
            workout = session.execute(
                select(Workout).where(Workout.id == workout_id)
            ).scalar_one_or_none()
            if workout:
                session.delete(workout)
                logger.info(
                    "Deleted orphaned workout during session cancellation",
                    workout_id=workout_id,
                    session_id=session_id,
                    user_id=user_id,
                )
        else:
            logger.info(
                "Keeping workout (referenced elsewhere) during session cancellation",
                workout_id=workout_id,
                session_id=session_id,
                activity_count=activity_count,
                other_planned_count=other_planned_count,
            )
    except Exception as e:
        logger.warning(
            "Failed to cleanup workout during cancellation (continuing with deletion)",
            workout_id=workout_id,
            session_id=session_id,
            user_id=user_id,
            error=str(e),
        )
        # Continue with deletion even if workout cleanup fails


def _handle_deleted_session(
    session: Session,
    session_id: str,
    user_id: str,
) -> Response:
    """Handle deletion by removing the session and cleaning up related resources."""
    # Find the planned session first
    try:
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()
    except (InternalError, ProgrammingError) as e:
        error_msg = str(e).lower()
        if "current transaction is aborted" in error_msg:
            logger.error(
                "Transaction aborted during SELECT for deletion, rolling back",
                session_id=session_id,
                user_id=user_id,
                error=str(e),
            )
            session.rollback()
        raise

    if not planned_session:
        raise HTTPException(status_code=404, detail="Planned session not found")

    # Unlink any session links
    try:
        unlink_by_planned(session, session_id, reason="Session deleted")
    except Exception as e:
        logger.warning(
            "Failed to unlink session during cancellation (continuing with deletion)",
            session_id=session_id,
            user_id=user_id,
            error=str(e),
        )
        # Continue with deletion even if unlinking fails

    # Handle cascading deletes for workout and workout_steps
    workout_id = planned_session.workout_id
    if workout_id:
        _delete_workout_if_orphaned(session, workout_id, session_id, user_id)

    # Delete planned session
    session.delete(planned_session)
    logger.info(
        "Deleted planned session",
        session_id=session_id,
        user_id=user_id,
    )
    # Return 204 No Content to indicate successful deletion
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _handle_transaction_error(
    session: Session,
    e: Exception,
    context: str,
    session_id: str,
    user_id: str,
) -> None:
    """Handle transaction errors by rolling back if needed and re-raising."""
    error_msg = str(e).lower()
    if "current transaction is aborted" in error_msg:
        logger.error(
            f"Transaction aborted during {context}, rolling back",
            session_id=session_id,
            user_id=user_id,
            error=str(e),
        )
        session.rollback()
    raise e


def _create_session_link_if_needed(
    session: Session,
    request: UpdateSessionStatusRequest,
    session_id: str,
    user_id: str,
) -> None:
    """Create SessionLink if marking as completed with activity ID."""
    if request.status == "completed" and request.completed_activity_id:
        try:
            upsert_link(
                session=session,
                user_id=user_id,
                planned_session_id=session_id,
                activity_id=request.completed_activity_id,
                status="confirmed",
                method="manual",
                confidence=1.0,
                notes="Status updated to completed via update_session_status endpoint",
            )
            logger.info(
                "Created session link when marking as completed",
                planned_session_id=session_id,
                activity_id=request.completed_activity_id,
                user_id=user_id,
            )
        except ValueError as e:
            logger.warning(
                "Failed to create session link",
                planned_session_id=session_id,
                activity_id=request.completed_activity_id,
                error=str(e),
                user_id=user_id,
            )
            raise HTTPException(
                status_code=404,
                detail=f"Activity not found or invalid: {e!s}",
            ) from e
        except (InternalError, ProgrammingError) as e:
            _handle_transaction_error(session, e, "upsert_link", session_id, user_id)


def _unlink_if_status_changed_from_completed(
    session: Session,
    old_status: str,
    new_status: str,
    session_id: str,
    user_id: str,
) -> None:
    """Unlink session if changing from completed to another status."""
    if old_status == "completed" and new_status != "completed":
        try:
            unlink_by_planned(session, session_id, reason=f"Status changed from completed to {new_status}")
        except Exception as e:
            logger.error(
                "Failed to unlink session, rolling back",
                session_id=session_id,
                user_id=user_id,
                error=str(e),
            )
            session.rollback()
            raise


def _update_session_with_workaround(
    session: Session,
    request: UpdateSessionStatusRequest,
    session_id: str,
    user_id: str,
) -> PlannedSession:
    """Update session status using raw SQL workaround when execution_notes or must_dos columns are missing."""
    try:
        # Check which columns exist
        inspector = inspect(session.bind)
        columns = [col["name"] for col in inspector.get_columns("planned_sessions")]
        has_execution_notes = "execution_notes" in columns
        has_must_dos = "must_dos" in columns

        # Build SELECT query based on available columns
        # Whitelist of allowed column names to prevent SQL injection
        allowed_columns = {
            "id", "user_id", "season_plan_id", "revision_id", "starts_at", "ends_at", "sport",
            "session_type", "title", "notes", "execution_notes", "must_dos",
            "duration_seconds", "distance_meters", "intensity", "intent", "workout_id",
            "status", "tags", "created_at", "updated_at"
        }
        select_fields = [
            "id", "user_id", "season_plan_id", "revision_id", "starts_at", "ends_at", "sport",
            "session_type", "title", "notes"
        ]
        if has_execution_notes:
            select_fields.append("execution_notes")
        if has_must_dos:
            select_fields.append("must_dos")
        select_fields.extend([
            "duration_seconds", "distance_meters", "intensity", "intent", "workout_id",
            "status", "tags", "created_at", "updated_at"
        ])
        # Validate all column names against whitelist
        for col in select_fields:
            if col not in allowed_columns:
                raise ValueError(f"Invalid column name: {col}")

        # Use quoted_name for safe column name quoting
        quoted_fields = [str(quoted_name(col, quote=True)) for col in select_fields]
        # Construct query using string concatenation to avoid f-string SQL injection warning
        # Column names are validated against whitelist above
        columns_str = ", ".join(quoted_fields)
        query_str = (
            "SELECT " + columns_str + " "
            "FROM planned_sessions "
            "WHERE id = :session_id AND user_id = :user_id"
        )
        result = session.execute(
            text(query_str),
            {"session_id": session_id, "user_id": user_id},
        ).fetchone()
    except (InternalError, ProgrammingError) as e:
        _handle_transaction_error(session, e, "SELECT", session_id, user_id)

    if not result:
        raise HTTPException(status_code=404, detail="Planned session not found")

    # Map result fields to indices based on which columns exist
    field_index = 0
    id_val = result[field_index]
    field_index += 1
    user_id_val = result[field_index]
    field_index += 1
    season_plan_id_val = result[field_index]
    field_index += 1
    revision_id_val = result[field_index]
    field_index += 1
    starts_at_val = result[field_index]
    field_index += 1
    ends_at_val = result[field_index]
    field_index += 1
    sport_val = result[field_index]
    field_index += 1
    session_type_val = result[field_index]
    field_index += 1
    title_val = result[field_index]
    field_index += 1
    notes_val = result[field_index]
    field_index += 1
    execution_notes_val = result[field_index] if has_execution_notes else None
    if has_execution_notes:
        field_index += 1
    must_dos_val = result[field_index] if has_must_dos else None
    if has_must_dos:
        field_index += 1
    duration_seconds_val = result[field_index]
    field_index += 1
    distance_meters_val = result[field_index]
    field_index += 1
    intensity_val = result[field_index]
    field_index += 1
    intent_val = result[field_index]
    field_index += 1
    workout_id_val = result[field_index]
    field_index += 1
    old_status = result[field_index]  # status
    field_index += 1
    tags_val = result[field_index]
    field_index += 1
    created_at_val = result[field_index]
    field_index += 1
    updated_at_val = result[field_index]
    field_index += 1

    # If changing from completed to another status, unlink the session
    _unlink_if_status_changed_from_completed(session, old_status, request.status, session_id, user_id)

    # Update status using raw SQL
    try:
        session.execute(
            text("""
                UPDATE planned_sessions
                SET status = :status, updated_at = NOW()
                WHERE id = :session_id AND user_id = :user_id
            """),
            {"status": request.status, "session_id": session_id, "user_id": user_id},
        )
    except (InternalError, ProgrammingError) as e:
        _handle_transaction_error(session, e, "UPDATE", session_id, user_id)

    # Schema v2: Create SessionLink if marking as completed with activity ID
    _create_session_link_if_needed(session, request, session_id, user_id)

    # Construct PlannedSession-like object for response
    return PlannedSession(
        id=id_val,
        user_id=user_id_val,
        season_plan_id=season_plan_id_val,
        revision_id=revision_id_val,
        starts_at=starts_at_val,
        ends_at=ends_at_val,
        sport=sport_val,
        session_type=session_type_val,
        title=title_val,
        notes=notes_val,
        execution_notes=execution_notes_val,
        must_dos=must_dos_val,
        duration_seconds=duration_seconds_val,
        distance_meters=distance_meters_val,
        intensity=intensity_val,
        intent=intent_val,
        workout_id=workout_id_val,
        status=request.status,  # Updated status
        tags=tags_val,
        created_at=created_at_val,
        updated_at=updated_at_val,
    )


def _update_session_normal(
    session: Session,
    planned_session: PlannedSession,
    request: UpdateSessionStatusRequest,
    session_id: str,
    user_id: str,
) -> PlannedSession:
    """Update session status using normal ORM path."""
    if not planned_session:
        raise HTTPException(status_code=404, detail="Planned session not found")

    # If changing from completed to another status, unlink the session
    _unlink_if_status_changed_from_completed(
        session, planned_session.status, request.status, session_id, user_id
    )

    # Update status
    planned_session.status = request.status

    # Schema v2: Create SessionLink if marking as completed with activity ID
    _create_session_link_if_needed(session, request, session_id, user_id)

    session.refresh(planned_session)
    return planned_session


@router.patch("/sessions/{session_id}/status", response_model=CalendarSession)
def update_session_status(
    session_id: str,
    request: UpdateSessionStatusRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update the status of a planned session.

    This endpoint allows marking planned sessions as completed, skipped, or deleted.
    When status is set to "deleted", the planned session is DELETED from the database.
    When marking as completed, you can optionally link it to an actual activity.

    Args:
        session_id: ID of the planned session to update
        request: Update request with new status
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated CalendarSession, or 204 No Content if status is "deleted" (session deleted)
    """
    logger.info(f"[CALENDAR] PATCH /calendar/sessions/{session_id}/status called for user_id={user_id}")

    valid_statuses = {"planned", "completed", "skipped", "deleted"}
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )

    with get_session() as session:
        # Special handling: if status is "deleted", delete the session instead of updating
        if request.status == "deleted":
            return _handle_deleted_session(session, session_id, user_id)
        # Find the planned session
        # Handle missing execution_notes or must_dos columns gracefully (migration may not have run yet)
        use_workaround = False
        try:
            planned_session = session.execute(
                select(PlannedSession).where(
                    PlannedSession.id == session_id,
                    PlannedSession.user_id == user_id,
                )
            ).scalar_one_or_none()
        except ProgrammingError as e:
            error_msg = str(e).lower()
            missing_columns = []
            if "execution_notes" in error_msg and ("does not exist" in error_msg or "undefinedcolumn" in error_msg):
                missing_columns.append("execution_notes")
            if "must_dos" in error_msg and ("does not exist" in error_msg or "undefinedcolumn" in error_msg):
                missing_columns.append("must_dos")

            if missing_columns:
                # Column(s) don't exist - use workaround with raw SQL
                # Rollback the transaction since it may be in a failed state
                session.rollback()
                logger.warning(
                    f"Missing columns {missing_columns}, using workaround",
                    session_id=session_id,
                    user_id=user_id,
                )
                use_workaround = True
            else:
                # Different error - rollback and re-raise
                session.rollback()
                raise
        except InternalError as e:
            # Transaction is in a failed state - rollback and re-raise
            # This can happen if a previous operation in the transaction failed
            error_msg = str(e).lower()
            if "current transaction is aborted" in error_msg:
                logger.error(
                    "Transaction aborted, rolling back",
                    session_id=session_id,
                    user_id=user_id,
                    error=str(e),
                )
                session.rollback()
            raise

        if use_workaround:
            planned_session = _update_session_with_workaround(session, request, session_id, user_id)
        else:
            if not planned_session:
                raise HTTPException(status_code=404, detail="Planned session not found")
            planned_session = _update_session_normal(session, planned_session, request, session_id, user_id)

        return _planned_session_to_calendar(planned_session)


# Separate router for planned-sessions endpoints (different prefix)
planned_sessions_router = APIRouter(prefix="/planned-sessions", tags=["planned-sessions"])


class UpdatePlannedSessionRequest(BaseModel):
    """Request to update a planned session (for drag/move operations)."""

    date: str | None = Field(default=None, description="New date in YYYY-MM-DD format")
    time: str | None = Field(default=None, description="New time in HH:MM format")


@planned_sessions_router.patch("/{planned_session_id}", response_model=CalendarSession)
def update_planned_session(
    planned_session_id: str,
    request: UpdatePlannedSessionRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update a planned session (date/time for drag/move operations).

    CANONICAL RULE: Only planned_sessions.id may be mutated.
    Calendar sessions, workouts, and activities are READ-ONLY views.

    This endpoint:
    - ONLY accepts planned_sessions.id
    - NEVER accepts workout_id, calendar_session_id, or activity_id
    - Returns 404 if the ID is not a valid planned_sessions.id

    Args:
        planned_session_id: MUST be a planned_sessions.id (not activity_id, workout_id, etc.)
        request: Update request with new date/time
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated CalendarSession

    Raises:
        HTTPException: 404 if planned_session_id is not a valid planned_sessions.id
    """
    # 🚨 HARD ASSERT: Prevent silent failure if frontend sends None
    if planned_session_id is None:
        raise HTTPException(
            status_code=400,
            detail="planned_session_id must not be None",
        )

    logger.info(
        f"[PLANNED-SESSIONS] PATCH /planned-sessions/{planned_session_id} called for user_id={user_id}"
    )

    with get_session() as session:
        # STRICT VALIDATION: Only query PlannedSession table
        # This ensures we ONLY accept planned_sessions.id
        # If the ID is an activity_id, workout_id, or calendar_session_id, this query will return None
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            # This correctly rejects:
            # - activity_id (not in planned_sessions table)
            # - workout_id (not in planned_sessions table)
            # - calendar_session_id (not in planned_sessions table)
            # - any other non-planned_sessions.id
            logger.warning(
                f"[PLANNED-SESSIONS] Planned session not found: id={planned_session_id}, user_id={user_id}. "
                "This ID is likely an activity_id, workout_id, or calendar_session_id (all are REJECTED)."
            )
            raise HTTPException(
                status_code=404,
                detail="Planned session not found. Only planned_sessions.id may be mutated.",
            )

        # Update date if provided
        if request.date is not None:
            try:
                # Parse date string (YYYY-MM-DD) using fromisoformat
                new_date = date.fromisoformat(request.date)
                # Convert to datetime (midnight UTC) preserving existing time if any
                existing_time = planned_session.date.time() if isinstance(planned_session.date, datetime) else datetime.min.time()
                planned_session.date = datetime.combine(new_date, existing_time).replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date format. Expected YYYY-MM-DD: {e!s}",
                ) from e

        # Update time if provided
        if request.time is not None:
            # Validate time format (HH:MM) by parsing it
            def validate_time_format(time_str: str) -> None:
                """Validate time format (HH:MM) and raise ValueError if invalid."""
                time_parts = time_str.split(":")
                if len(time_parts) != 2:
                    raise ValueError("Time must be in HH:MM format")
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError("Hour must be 0-23, minute must be 0-59")

            try:
                validate_time_format(request.time)
                planned_session.time = request.time
            except (ValueError, IndexError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid time format. Expected HH:MM: {e!s}",
                ) from e

        session.commit()
        session.refresh(planned_session)

        return _planned_session_to_calendar(planned_session)


@planned_sessions_router.delete("/{planned_session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned_session(
    planned_session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Delete a planned session.

    CANONICAL RULE: Only planned_sessions.id may be deleted.
    Calendar sessions, workouts, and activities are READ-ONLY views.

    This endpoint:
    - ONLY accepts planned_sessions.id
    - NEVER accepts workout_id, calendar_session_id, or activity_id
    - Returns 404 if the ID is not a valid planned_sessions.id
    - Properly handles cascading deletes for workout and workout_steps

    Args:
        planned_session_id: MUST be a planned_sessions.id (not activity_id, workout_id, etc.)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        None (204 No Content on success)

    Raises:
        HTTPException: 404 if planned_session_id is not a valid planned_sessions.id
    """
    logger.info(
        f"[PLANNED-SESSIONS] DELETE /planned-sessions/{planned_session_id} called for user_id={user_id}"
    )

    with get_session() as session:
        # STRICT VALIDATION: Only query PlannedSession table
        # This ensures we ONLY accept planned_sessions.id
        # If the ID is an activity_id, workout_id, or calendar_session_id, this query will return None
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            # This correctly rejects:
            # - activity_id (not in planned_sessions table)
            # - workout_id (not in planned_sessions table)
            # - calendar_session_id (not in planned_sessions table)
            # - any other non-planned_sessions.id
            logger.warning(
                f"[PLANNED-SESSIONS] Planned session not found for deletion: id={planned_session_id}, user_id={user_id}. "
                "This ID is likely an activity_id, workout_id, or calendar_session_id (all are REJECTED)."
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Planned session not found",
            )

        # Block deletion if session is locked (has completed_activity_id)
        if planned_session.is_locked:
            logger.warning(
                f"[PLANNED-SESSIONS] Attempted to delete locked session: id={planned_session_id}, user_id={user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete a session that has been completed and merged with an activity. Unpair the activity first.",
            )

        # Handle cascading deletes for workout and workout_steps
        # Only delete workout if it's not referenced by any activities
        workout_id = planned_session.workout_id
        if workout_id:
            # Check if workout is referenced by any activities
            # Schema v2: activity.workout_id does not exist - check through workout_executions instead
            activity_count = session.execute(
                select(func.count(WorkoutExecution.id)).where(WorkoutExecution.workout_id == workout_id)
            ).scalar() or 0

            # Check if workout is referenced by any other planned sessions
            other_planned_count = session.execute(
                select(func.count(PlannedSession.id)).where(
                    PlannedSession.workout_id == workout_id,
                    PlannedSession.id != planned_session_id,
                )
            ).scalar() or 0

            # Only delete workout if it's not referenced elsewhere
            if activity_count == 0 and other_planned_count == 0:
                # Delete workout_steps first (cascade order)
                workout_steps = session.execute(
                    select(WorkoutStep).where(WorkoutStep.workout_id == workout_id)
                ).scalars().all()
                for step in workout_steps:
                    session.delete(step)

                # Delete workout
                workout = session.execute(
                    select(Workout).where(Workout.id == workout_id)
                ).scalar_one_or_none()
                if workout:
                    session.delete(workout)
                    logger.info(
                        f"[PLANNED-SESSIONS] Deleted orphaned workout: workout_id={workout_id}, user_id={user_id}"
                    )
            else:
                logger.info(
                    f"[PLANNED-SESSIONS] Keeping workout (referenced elsewhere): workout_id={workout_id}, "
                    f"activity_count={activity_count}, other_planned_count={other_planned_count}"
                )

        # Delete planned session
        session.delete(planned_session)
        session.commit()
        logger.info(
            f"[PLANNED-SESSIONS] Deleted planned session: id={planned_session_id}, user_id={user_id}"
        )
