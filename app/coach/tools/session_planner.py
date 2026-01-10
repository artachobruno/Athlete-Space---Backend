"""Helper functions for generating and storing planned training sessions."""

from datetime import date, datetime, timedelta, timezone
from typing import NoReturn

from loguru import logger
from sqlalchemy import select

from app.coach.mcp_client import MCPError, call_tool
from app.db.models import PlannedSession
from app.db.session import get_session


def _raise_timezone_naive_error(session_date_raw: str) -> None:
    """Raise error for timezone-naive date from ISO string."""
    raise ValueError(f"Timezone-naive date from ISO string: {session_date_raw}")


def _raise_mcp_save_failed_error(expected_count: int) -> NoReturn:
    """Raise error when MCP tool fails to save sessions.

    This function always raises RuntimeError and never returns.
    """
    raise RuntimeError(
        f"Failed to save planned sessions: MCP tool returned saved_count=0. "
        f"Expected to save {expected_count} sessions but none were saved."
    )


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

    saved_count = 0
    with get_session() as session:
        try:
            for session_data in sessions:
                session_date_raw = session_data.get("date")
                parsed_date = _parse_session_date(session_date_raw)
                if parsed_date is None:
                    continue

                # Check if session already exists (same user, athlete, date, and title)
                existing = session.scalar(
                    select(PlannedSession).where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.date == parsed_date,
                        PlannedSession.title == session_data.get("title"),
                    )
                )

                if existing:
                    logger.debug(
                        "Skipping duplicate planned session",
                        user_id=user_id,
                        athlete_id=athlete_id,
                        date=parsed_date.isoformat(),
                        title=session_data.get("title"),
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
                saved_count += 1

            session.commit()

            logger.info(
                "Saved planned sessions to database",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                total_sessions=len(sessions),
                plan_type=plan_type,
                plan_id=plan_id,
            )

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
) -> int:
    """Save planned training sessions to the database via MCP.

    This function calls the MCP tool for saving sessions.
    For direct database access, use save_sessions_to_database instead.

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
        Number of sessions saved
    """
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
        return 0

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

    try:
        logger.debug(
            "session_planner: Calling MCP tool save_planned_sessions",
            user_id=user_id,
            athlete_id=athlete_id,
            session_count=len(sessions_for_mcp),
            plan_type=plan_type,
            plan_id=plan_id,
        )
        result = await call_tool(
            "save_planned_sessions",
            {
                "user_id": user_id,
                "athlete_id": athlete_id,
                "sessions": sessions_for_mcp,
                "plan_type": plan_type,
                "plan_id": plan_id,
            },
        )
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
        if saved_count == 0:
            _raise_mcp_save_failed_error(len(sessions))
        else:
            logger.info(
                "Saved planned sessions via MCP",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                plan_type=plan_type,
            )
            return saved_count
    except MCPError as e:
        logger.error(
            "Failed to persist planned sessions via MCP — plan creation failed",
            error_code=e.code,
            error_message=e.message,
            user_id=user_id,
            athlete_id=athlete_id,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to save training plan: MCP error {e.code}: {e.message}"
        ) from e
    except Exception as e:
        logger.error(
            "Unexpected error persisting planned sessions — plan creation failed",
            error_type=type(e).__name__,
            error_message=str(e),
            user_id=user_id,
            athlete_id=athlete_id,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to save training plan: {type(e).__name__}: {e!s}"
        ) from e
