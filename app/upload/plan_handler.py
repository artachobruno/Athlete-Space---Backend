"""Handler for training plan uploads from chat.

Handles creating planned sessions from parsed upload data and persisting to database.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from app.coach.tools.session_planner import save_sessions_to_database
from app.upload.plan_parser import ParsedSessionUpload, parse_plan_upload


def _validate_plan_dates(sessions: list[ParsedSessionUpload]) -> tuple[bool, str | None]:
    """Validate plan dates for coverage and overlaps.

    Args:
        sessions: List of parsed sessions

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sessions:
        return False, "No sessions in plan"

    # Check for date coverage (at least one session)
    if len(sessions) == 0:
        return False, "Empty plan"

    # Check for obvious overlaps (same date and time)
    seen_dates_times: set[tuple[datetime, str | None]] = set()
    for session in sessions:
        key = (session.date, session.time)
        if key in seen_dates_times:
            logger.warning(f"Overlapping session detected: {session.date} {session.time}")
            # Allow overlaps, just log warning
        seen_dates_times.add(key)

    return True, None


def upload_plan_from_chat(
    user_id: str,
    athlete_id: int,
    content: str,
) -> tuple[int, str]:
    """Upload training plan from chat content (CSV or text).

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID
        content: CSV content or free text

    Returns:
        Tuple of (count of sessions created, summary message)

    Raises:
        ValueError: If parsing or validation fails
    """
    logger.info(f"[UPLOAD_PLAN] Processing plan upload for user_id={user_id}, athlete_id={athlete_id}")

    # Parse sessions
    base_date = datetime.now(timezone.utc)
    parsed_sessions = parse_plan_upload(content, base_date)
    logger.info(f"[UPLOAD_PLAN] Parsed {len(parsed_sessions)} sessions")

    # Validate plan
    is_valid, error_msg = _validate_plan_dates(parsed_sessions)
    if not is_valid and error_msg:
        raise ValueError(f"Plan validation failed: {error_msg}")

    # Convert to session dictionaries for save_sessions_to_database
    session_dicts: list[dict] = []
    for parsed in parsed_sessions:
        session_dict = {
            "date": parsed.date,
            "time": parsed.time,
            "type": parsed.type,
            "title": parsed.title,
            "duration_minutes": parsed.duration_minutes,
            "distance_km": parsed.distance_km,
            "intensity": parsed.intensity,
            "notes": parsed.notes,
            "week_number": parsed.week_number,
        }
        session_dicts.append(session_dict)

    # Save sessions to database
    saved_count = save_sessions_to_database(
        user_id=user_id,
        athlete_id=athlete_id,
        sessions=session_dicts,
        plan_type="chat_upload",
        plan_id=None,
    )

    logger.info(f"[UPLOAD_PLAN] Upload complete: {saved_count} sessions saved")

    # Generate summary message
    if saved_count == len(parsed_sessions):
        summary = f"Successfully uploaded {saved_count} training sessions to your calendar."
    else:
        summary = f"Uploaded {saved_count} of {len(parsed_sessions)} sessions (some may have been duplicates)."

    return saved_count, summary
