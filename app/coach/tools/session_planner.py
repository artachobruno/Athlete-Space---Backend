"""Helper functions for generating and storing planned training sessions."""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.calendar.conflicts import (
    auto_shift_sessions,
    detect_conflicts,
    get_resolution_mode,
)
from app.coach.mcp_client import call_tool_safe
from app.coach.mcp_health import mcp_is_healthy
from app.db.models import PlannedSession
from app.db.session import get_session
from app.pairing.auto_pairing_service import try_auto_pair
from app.persistence.retry.queue import enqueue_retry
from app.persistence.retry.types import PlannedSessionRetryJob
from app.workouts.guards import assert_planned_session_has_workout
from app.workouts.workout_factory import WorkoutFactory


def _raise_timezone_naive_error(session_date_raw: str) -> None:
    """Raise error for timezone-naive date from ISO string."""
    raise ValueError(f"Timezone-naive date from ISO string: {session_date_raw}")


def _parse_session_date(session_date_raw: date | datetime | str | None) -> datetime | None:
    """Parse session date from various formats.

    Phase 6: Preserve exactly what LLM returns - no timezone mutation.
    Dates are already validated as timezone-aware in LLM generation.

    Args:
        session_date_raw: Date in string, date, or datetime format, or None

    Returns:
        Parsed datetime with preserved timezone, or None if parsing fails or input is None
    """
    if session_date_raw is None:
        return None

    if isinstance(session_date_raw, str):
        try:
            # Preserve timezone from ISO string - no normalization
            if "T" in session_date_raw:
                parsed_date = datetime.fromisoformat(session_date_raw.replace("Z", "+00:00"))
                # Validate timezone-aware (already enforced by LLM validation)
                if parsed_date.tzinfo is None:
                    _raise_timezone_naive_error(session_date_raw)
            else:
                # Date-only string - this shouldn't happen if LLM returns timezone-aware dates
                parsed_date = datetime.strptime(session_date_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                logger.warning(f"Received date-only string, defaulting to UTC: {session_date_raw}")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse date '{session_date_raw}': {e}")
            return None
        else:
            return parsed_date

    if isinstance(session_date_raw, date) and not isinstance(session_date_raw, datetime):
        # Date-only - shouldn't happen if LLM returns timezone-aware datetimes
        logger.warning(f"Received date-only object, defaulting to UTC: {session_date_raw}")
        return datetime.combine(session_date_raw, datetime.min.time()).replace(tzinfo=timezone.utc)

    if isinstance(session_date_raw, datetime):
        # Phase 6: Preserve exact timezone from LLM - no mutation
        if session_date_raw.tzinfo is None:
            # This should not happen - validation already ensures timezone-aware
            logger.error(f"Timezone-naive datetime received despite validation: {session_date_raw}")
            raise ValueError(f"Date must be timezone-aware: {session_date_raw}")
        return session_date_raw

    logger.warning(f"Invalid date type for session: {type(session_date_raw)}")
    return None


def save_sessions_to_database(
    user_id: str,
    athlete_id: int,
    sessions: list[dict],
    plan_type: str,
    plan_id: str | None = None,
) -> int:
    """Save planned training sessions directly to the database.

    This is the actual implementation that saves to the database.
    Used by MCP tools and other internal functions.

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID (Strava)
        sessions: List of session dictionaries with keys:
            - date: datetime, date, or ISO string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
            - time: Optional time string (HH:MM)
            - type: Activity type (Run, Bike, etc.)
            - title: Session title
            - duration_minutes: Optional duration
            - distance_km: Optional distance
            - intensity: Optional intensity (easy, moderate, hard, race)
            - notes: Optional notes
            - week_number: Optional week number in plan
        plan_type: Type of plan ("race", "season", "weekly", "single")
        plan_id: Optional plan identifier

    Returns:
        Number of sessions actually saved (excluding duplicates)
    """
    if not sessions:
        logger.warning("No sessions to save")
        return 0

    # Phase 6: Invariant checks before saving
    # No duplicate dates+titles, all dates timezone-aware
    seen_dates_titles: set[tuple[datetime, str]] = set()
    for session_data in sessions:
        session_date_raw = session_data.get("date")
        parsed_date = _parse_session_date(session_date_raw)
        if parsed_date is None:
            raise ValueError(f"Invalid date in session: {session_data.get('title', 'unknown')}")

        # Phase 6: All dates must be timezone-aware
        if parsed_date.tzinfo is None:
            raise ValueError(f"Date must be timezone-aware: {parsed_date}")

        title = session_data.get("title")
        if not title:
            raise ValueError("Session title is required")

        # Phase 6: No duplicate dates+titles
        date_title_key = (parsed_date, title)
        if date_title_key in seen_dates_titles:
            raise ValueError(f"Duplicate session detected: {title} on {parsed_date.isoformat()}")
        seen_dates_titles.add(date_title_key)

    # A86: Conflict detection and resolution
    # Determine resolution mode
    resolution_mode = get_resolution_mode(plan_type)

    # Get date range for querying existing sessions
    session_dates = []
    for session_data in sessions:
        parsed_date = _parse_session_date(session_data.get("date"))
        if parsed_date:
            session_dates.append(parsed_date)

    if not session_dates:
        logger.warning("No valid dates in sessions for conflict detection")
        return 0

    min_date = min(session_dates).date()
    max_date = max(session_dates).date()
    # Expand range by MAX_SHIFT_DAYS to account for potential shifts
    min_date -= timedelta(days=3)
    max_date += timedelta(days=3)

    # Fetch existing sessions for conflict detection
    with get_session() as db_session:
        existing_sessions = db_session.execute(
            select(PlannedSession)
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.athlete_id == athlete_id,
                PlannedSession.date >= datetime.combine(min_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                PlannedSession.date <= datetime.combine(max_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
            .order_by(PlannedSession.date)
        ).scalars().all()
        existing_sessions_list = list(existing_sessions)

    # Detect conflicts
    # Type cast: sessions is list[dict] which is compatible with list[PlannedSession | dict[str, Any]]
    conflicts = detect_conflicts(existing_sessions_list, cast("list[PlannedSession | dict[str, Any]]", sessions))

    # Handle conflicts based on resolution mode
    sessions_to_save = sessions
    if conflicts:
        if resolution_mode == "auto_shift":
            # Try to auto-shift sessions
            shifted_sessions, unresolved_conflicts = auto_shift_sessions(
                candidate_sessions=sessions,
                existing_sessions=existing_sessions_list,
            )
            if unresolved_conflicts:
                # Some conflicts couldn't be resolved
                conflict_summary = f"Found {len(unresolved_conflicts)} unresolved conflicts after auto-shift"
                logger.warning(
                    conflict_summary,
                    user_id=user_id,
                    athlete_id=athlete_id,
                    unresolved_count=len(unresolved_conflicts),
                )
                raise ValueError(
                    f"{conflict_summary}. Please review your calendar and try again. "
                    f"Conflicts: {', '.join(f'{c.candidate_session_title} on {c.date}' for c in unresolved_conflicts[:3])}"
                )
            sessions_to_save = shifted_sessions
            logger.info(
                "Auto-shifted sessions to resolve conflicts",
                user_id=user_id,
                athlete_id=athlete_id,
                original_count=len(sessions),
                shifted_count=len(shifted_sessions),
            )
        else:
            # require_user_confirmation mode - don't save, raise error with conflict info
            conflict_summary = f"Found {len(conflicts)} conflicts that require user confirmation"
            logger.warning(
                conflict_summary,
                user_id=user_id,
                athlete_id=athlete_id,
                conflict_count=len(conflicts),
            )
            # For now, raise ValueError with conflict details
            # TODO: Return structured conflict info in response schema (A86.5)
            conflict_details = ", ".join(
                f"{c.candidate_session_title} conflicts with {c.existing_session_title} on {c.date}"
                for c in conflicts[:3]
            )
            raise ValueError(
                f"{conflict_summary}. Conflicts: {conflict_details}"
                + (f" (and {len(conflicts) - 3} more)" if len(conflicts) > 3 else "")
            )

    saved_count = 0
    with get_session() as session:
        try:
            for session_data in sessions_to_save:
                session_date_raw = session_data.get("date")
                parsed_date = _parse_session_date(session_date_raw)
                if parsed_date is None:
                    continue

                # Check if session already exists (same user, athlete, date, and title)
                # Also check plan_id if provided for idempotency
                query = select(PlannedSession).where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.athlete_id == athlete_id,
                    PlannedSession.date == parsed_date,
                    PlannedSession.title == session_data.get("title"),
                )
                if plan_id:
                    query = query.where(PlannedSession.plan_id == plan_id)

                existing = session.scalar(query)

                if existing:
                    logger.debug(
                        "Skipping duplicate planned session",
                        user_id=user_id,
                        athlete_id=athlete_id,
                        date=parsed_date.isoformat(),
                        title=session_data.get("title"),
                        plan_id=plan_id,
                    )
                    continue

                # Phase 6: Persist exactly what LLM returns - no field mutation, no auto-fill
                # Only extract fields that exist in session_data - no defaults
                planned_session = PlannedSession(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    date=parsed_date,
                    time=session_data.get("time"),  # Exactly as LLM provided
                    type=session_data.get("type"),  # Exactly as LLM provided
                    title=session_data.get("title"),  # Exactly as LLM provided
                    duration_minutes=session_data.get("duration_minutes"),  # Exactly as LLM provided
                    distance_km=session_data.get("distance_km"),  # Exactly as LLM provided
                    intensity=session_data.get("intensity"),  # Exactly as LLM provided
                    notes=session_data.get("notes") or session_data.get("description"),  # Use description if notes not provided
                    plan_type=plan_type,
                    plan_id=plan_id,
                    week_number=session_data.get("week_number"),  # Exactly as LLM provided
                    status="planned",
                    completed=False,
                )

                session.add(planned_session)
                session.flush()  # Ensure ID is generated

                # PHASE 2: Enforce workout creation (mandatory invariant)
                WorkoutFactory.get_or_create_for_planned_session(session, planned_session)

                # Attempt auto-pairing with activities
                try:
                    try_auto_pair(planned=planned_session, session=session)
                except Exception as e:
                    logger.warning(
                        f"Auto-pairing failed for planned session {planned_session.id}: {e}",
                        user_id=user_id,
                        athlete_id=athlete_id,
                    )

                saved_count += 1

            try:
                session.commit()
            except IntegrityError as e:
                # Handle unique constraint violations (idempotency for retries)
                session.rollback()
                logger.warning(
                    "Unique constraint violation during save (likely duplicate from retry)",
                    user_id=user_id,
                    athlete_id=athlete_id,
                    plan_id=plan_id,
                    error=str(e),
                )
                # Re-check which sessions were actually saved before the error
                # This is a best-effort count - some may have been saved
                saved_count = 0
                for session_data in sessions_to_save:
                    session_date_raw = session_data.get("date")
                    parsed_date = _parse_session_date(session_date_raw)
                    if parsed_date is None:
                        continue
                    query = select(PlannedSession).where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.date == parsed_date,
                        PlannedSession.title == session_data.get("title"),
                    )
                    if plan_id:
                        query = query.where(PlannedSession.plan_id == plan_id)
                    if session.scalar(query):
                        saved_count += 1
                logger.info(
                    "Recovered from unique constraint violation",
                    user_id=user_id,
                    athlete_id=athlete_id,
                    plan_id=plan_id,
                    saved_count=saved_count,
                )

            logger.info(
                "Saved planned sessions to database",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                total_sessions=len(sessions),
                plan_type=plan_type,
                plan_id=plan_id,
            )

            # PHASE 7: Assert invariant holds (guard check)
            try:
                for session_data in sessions_to_save:
                    parsed_date = _parse_session_date(session_data.get("date"))
                    if parsed_date is None:
                        continue
                    query = select(PlannedSession).where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.date == parsed_date,
                        PlannedSession.title == session_data.get("title"),
                    )
                    if plan_id:
                        query = query.where(PlannedSession.plan_id == plan_id)
                    saved_session = session.scalar(query)
                    if saved_session:
                        assert_planned_session_has_workout(saved_session)
            except AssertionError:
                # Log but don't fail the request - invariant violation is logged
                pass

        except Exception as e:
            session.rollback()
            logger.error(
                "Failed to save planned sessions to database",
                user_id=user_id,
                athlete_id=athlete_id,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            raise

    return saved_count


async def save_planned_sessions(
    user_id: str,
    athlete_id: int,
    sessions: list[dict],
    plan_type: str,
    plan_id: str | None = None,
) -> dict[str, int | str]:
    """Save planned training sessions to the database via MCP.

    This function calls the MCP tool for saving sessions.
    For direct database access, use save_sessions_to_database instead.

    IMPORTANT: This function NEVER raises exceptions. Persistence failures are
    logged and marked as degraded, but the plan is always returned.

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID (Strava)
        sessions: List of session dictionaries with keys:
            - date: datetime or date string (YYYY-MM-DD)
            - time: Optional time string (HH:MM)
            - type: Activity type (Run, Bike, etc.)
            - title: Session title
            - duration_minutes: Optional duration
            - distance_km: Optional distance
            - intensity: Optional intensity (easy, moderate, hard, race)
            - notes: Optional notes
            - week_number: Optional week number in plan
        plan_type: Type of plan ("race" or "season")
        plan_id: Optional plan identifier

    Returns:
        Dictionary with:
            - saved_count: Number of sessions saved (0 if failed)
            - persistence_status: "saved" if successful, "degraded" if failed
    """
    persistence_status = "saved"

    logger.debug(
        "session_planner: Starting save_planned_sessions",
        user_id=user_id,
        athlete_id=athlete_id,
        session_count=len(sessions) if sessions else 0,
        plan_type=plan_type,
        plan_id=plan_id,
    )

    if not sessions:
        logger.debug("session_planner: No sessions to save, returning 0")
        logger.warning("No sessions to save")
        return {"saved_count": 0, "persistence_status": "saved"}

    # Circuit breaker: Check MCP health before attempting call
    if not mcp_is_healthy():
        logger.warning(
            "MCP circuit breaker open - skipping persistence call",
            user_id=user_id,
            athlete_id=athlete_id,
            plan_id=plan_id,
        )
        # Enqueue retry job (best-effort, never blocks)
        if plan_id:
            try:
                # Convert sessions to MCP format for retry
                sessions_for_retry = []
                for session_data in sessions:
                    mcp_session = session_data.copy()
                    session_date = mcp_session.get("date")
                    if isinstance(session_date, (datetime, date)):
                        mcp_session["date"] = session_date.isoformat()
                    sessions_for_retry.append(mcp_session)
                enqueue_retry(
                    PlannedSessionRetryJob(
                        plan_id=plan_id,
                        user_id=user_id,
                        athlete_id=athlete_id,
                        sessions=sessions_for_retry,
                        plan_type=plan_type,
                        created_at=time.time(),
                        attempts=0,
                    )
                )
            except Exception:
                logger.warning("Failed to enqueue persistence retry", exc_info=True)
        return {"saved_count": 0, "persistence_status": "degraded"}

    try:
        # Convert datetime objects to ISO strings for MCP
        logger.debug(
            "session_planner: Converting sessions for MCP",
            total_sessions=len(sessions),
        )
        sessions_for_mcp = []
        for idx, session_data in enumerate(sessions):
            logger.debug(
                "session_planner: Converting session for MCP",
                index=idx,
                session_keys=list(session_data.keys()) if isinstance(session_data, dict) else None,
                has_date="date" in session_data if isinstance(session_data, dict) else False,
            )
            mcp_session = session_data.copy()
            # Convert date to ISO string if it's a datetime
            session_date = mcp_session.get("date")
            if isinstance(session_date, (datetime, date)):
                original_date = session_date
                mcp_session["date"] = session_date.isoformat()
                logger.debug(
                    "session_planner: Converted date to ISO string",
                    index=idx,
                    original_date_type=type(original_date).__name__,
                    iso_string=mcp_session["date"],
                )
            sessions_for_mcp.append(mcp_session)
        logger.debug(
            "session_planner: Sessions converted for MCP",
            total_sessions=len(sessions_for_mcp),
            first_session_date=sessions_for_mcp[0].get("date") if sessions_for_mcp else None,
        )

        logger.debug(
            "session_planner: Calling MCP tool save_planned_sessions",
            user_id=user_id,
            athlete_id=athlete_id,
            session_count=len(sessions_for_mcp),
            plan_type=plan_type,
            plan_id=plan_id,
        )
        result = await call_tool_safe(
            "save_planned_sessions",
            {
                "user_id": user_id,
                "athlete_id": athlete_id,
                "sessions": sessions_for_mcp,
                "plan_type": plan_type,
                "plan_id": plan_id,
            },
        )

        if result is None:
            logger.error(
                "Failed to persist planned sessions via MCP — continuing",
                extra={
                    "user_id": user_id,
                    "athlete_id": athlete_id,
                    "plan_id": plan_id,
                    "session_count": len(sessions),
                    "plan_type": plan_type,
                },
            )
            persistence_status = "degraded"
            # Enqueue retry job (best-effort, never blocks)
            if plan_id:
                try:
                    enqueue_retry(
                        PlannedSessionRetryJob(
                            plan_id=plan_id,
                            user_id=user_id,
                            athlete_id=athlete_id,
                            sessions=sessions_for_mcp,  # Use MCP-formatted sessions
                            plan_type=plan_type,
                            created_at=time.time(),
                            attempts=0,
                        )
                    )
                except Exception:
                    logger.warning("Failed to enqueue persistence retry", exc_info=True)
            return {"saved_count": 0, "persistence_status": persistence_status}

        logger.debug(
            "session_planner: MCP tool save_planned_sessions completed",
            result_keys=list(result.keys()) if isinstance(result, dict) else None,
            has_saved_count="saved_count" in result if isinstance(result, dict) else False,
        )
        saved_count = result.get("saved_count", 0)
        logger.debug(
            "session_planner: Extracted saved_count from result",
            saved_count=saved_count,
            expected_count=len(sessions),
        )

        if saved_count > 0:
            logger.info(
                "Saved planned sessions via MCP",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                plan_type=plan_type,
            )
        else:
            logger.warning(
                "MCP tool returned saved_count=0 (degraded mode)",
                user_id=user_id,
                athlete_id=athlete_id,
                expected_count=len(sessions),
                plan_type=plan_type,
            )
            persistence_status = "degraded"
            # Enqueue retry job (best-effort, never blocks)
            if plan_id:
                try:
                    enqueue_retry(
                        PlannedSessionRetryJob(
                            plan_id=plan_id,
                            user_id=user_id,
                            athlete_id=athlete_id,
                            sessions=sessions_for_mcp,  # Use MCP-formatted sessions
                            plan_type=plan_type,
                            created_at=time.time(),
                            attempts=0,
                        )
                    )
                except Exception:
                    logger.warning("Failed to enqueue persistence retry", exc_info=True)
    except Exception as e:
        logger.error(
            "Failed to persist planned sessions via MCP — continuing",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "plan_id": plan_id,
                "user_id": user_id,
                "athlete_id": athlete_id,
                "session_count": len(sessions),
                "plan_type": plan_type,
            },
            exc_info=True,
        )
        persistence_status = "degraded"
        # Enqueue retry job (best-effort, never blocks)
        if plan_id:
            try:
                # Convert sessions to MCP format if not already done
                sessions_for_retry = []
                for session_data in sessions:
                    mcp_session = session_data.copy()
                    session_date = mcp_session.get("date")
                    if isinstance(session_date, (datetime, date)):
                        mcp_session["date"] = session_date.isoformat()
                    sessions_for_retry.append(mcp_session)
                enqueue_retry(
                    PlannedSessionRetryJob(
                        plan_id=plan_id,
                        user_id=user_id,
                        athlete_id=athlete_id,
                        sessions=sessions_for_retry,
                        plan_type=plan_type,
                        created_at=time.time(),
                        attempts=0,
                    )
                )
            except Exception:
                logger.warning("Failed to enqueue persistence retry", exc_info=True)
        return {"saved_count": 0, "persistence_status": persistence_status}
    else:
        return {"saved_count": saved_count, "persistence_status": persistence_status}
