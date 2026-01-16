"""Helper functions for querying calendar_items view (schema v2).

This module provides unified access to planned sessions and activities
via the calendar_items view, avoiding schema mismatches.
"""

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas.schemas import CalendarSession

# Schema v2: Use calendar_items view for unified querying
SQL_CALENDAR_ITEMS = text("""
SELECT item_id, kind, starts_at, ends_at, sport, title, status, payload
FROM calendar_items
WHERE user_id = :user_id
  AND starts_at >= :start
  AND starts_at < :end
ORDER BY starts_at ASC
""")


def get_calendar_items_from_view(
    session: Session,
    user_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Get calendar items from calendar_items view (schema v2).

    This function queries the unified calendar_items view which contains
    both planned sessions and activities. This avoids schema mismatches
    and ensures consistency.

    Args:
        session: Database session
        user_id: User ID
        start: Start datetime (inclusive, UTC)
        end: End datetime (exclusive, UTC)

    Returns:
        List of calendar item dictionaries with keys:
        - kind: 'planned' or 'activity'
        - starts_at: datetime
        - ends_at: datetime | None
        - sport: str | None
        - title: str | None
        - status: str
        - payload: dict with item-specific data
    """
    try:
        rows = session.execute(
            SQL_CALENDAR_ITEMS,
            {"user_id": user_id, "start": start, "end": end},
        ).mappings().all()

        # Convert RowMapping objects to dicts (ensure all values are accessible)
        items = []
        for row in rows:
            item_dict: dict[str, Any] = {}
            for key in row.keys():
                item_dict[key] = row[key]
            items.append(item_dict)

        logger.debug(
            f"Fetched {len(items)} calendar items from view",
            user_id=user_id,
            start=start,
            end=end,
            planned_count=sum(1 for item in items if item.get("kind") == "planned"),
            activity_count=sum(1 for item in items if item.get("kind") == "activity"),
        )

        return items
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg or "relation" in error_msg:
            logger.warning(
                f"[CALENDAR] calendar_items view does not exist or schema mismatch. Returning empty: {e!r}"
            )
            session.rollback()
            return []
        logger.exception(f"[CALENDAR] Failed to query calendar_items view: {e!r}")
        raise


def calendar_session_from_view_row(row: dict[str, Any]) -> CalendarSession:
    """Convert a calendar_items view row to CalendarSession DTO (schema v2).

    Args:
        row: Dictionary with keys: item_id, kind, starts_at, ends_at, sport, title, status, payload

    Returns:
        CalendarSession object with mapped fields
    """
    # Extract fields from row
    item_id = str(row["item_id"])
    kind = str(row["kind"])
    starts_at: datetime | None = row.get("starts_at")
    ends_at: datetime | None = row.get("ends_at")
    sport: str | None = row.get("sport")
    title: str | None = row.get("title")
    status: str = str(row.get("status", "planned"))
    payload: dict[str, Any] = row.get("payload") or {}

    # Map starts_at to date and time
    if starts_at:
        if isinstance(starts_at, datetime):
            date_str = starts_at.strftime("%Y-%m-%d")
            time_str = starts_at.strftime("%H:%M")
        else:
            # Fallback if starts_at is already a string
            date_str = str(starts_at)[:10] if len(str(starts_at)) >= 10 else ""
            time_str = str(starts_at)[11:16] if len(str(starts_at)) >= 16 else None
    else:
        date_str = ""
        time_str = None

    # Map sport to type (capitalize first letter)
    type_str = sport.capitalize() if sport else "Activity"

    # Extract duration_seconds and distance_meters from payload
    duration_seconds: int | None = payload.get("duration_seconds")
    distance_meters: float | None = payload.get("distance_meters")

    # Convert duration_seconds to duration_minutes
    duration_minutes: int | None = None
    if duration_seconds is not None:
        duration_minutes = int(duration_seconds // 60)

    # Convert distance_meters to distance_km
    distance_km: float | None = None
    if distance_meters is not None and distance_meters > 0:
        distance_km = round(float(distance_meters) / 1000.0, 2)

    # Extract other fields from payload
    workout_id: str | None = payload.get("workout_id")
    notes: str | None = None  # Notes not in view payload currently

    # Determine completed flag and completed_at
    completed = kind == "activity" or status == "completed"
    completed_at_str: str | None = None
    if completed and starts_at:
        if isinstance(starts_at, datetime):
            completed_at_str = starts_at.isoformat()
        else:
            completed_at_str = str(starts_at)

    # Extract activity_id from payload for activities
    completed_activity_id: str | None = None
    if kind == "activity":
        completed_activity_id = payload.get("activity_id")

    # Determine intensity (simple heuristic based on duration)
    intensity: str | None = None
    if duration_minutes:
        duration_hours = duration_minutes / 60.0
        if duration_hours > 1.5:
            intensity = "easy"
        elif duration_hours > 0.75:
            intensity = "moderate"
        else:
            intensity = "hard"

    # For activities without title, generate from sport + duration
    if not title and kind == "activity":
        title = f"{type_str} - {duration_minutes}min" if duration_minutes else type_str
    elif not title:
        title = type_str

    return CalendarSession(
        id=item_id,
        date=date_str,
        time=time_str,
        type=type_str,
        title=title or "",
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        intensity=intensity,
        status=status,
        notes=notes,
        workout_id=workout_id,
        completed_activity_id=completed_activity_id,
        completed=completed,
        completed_at=completed_at_str,
    )
