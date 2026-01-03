"""Save activity records to the database.

LEGACY: This module maps from old ActivityRecord format to new Activity model.
The new ingestion system (ingestion_strava.py, background_sync.py) directly uses the new Activity schema.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.state.models import Activity, StravaAccount
from models.activity import ActivityRecord


def _get_user_id_from_athlete_id(session: Session, athlete_id: int) -> str | None:
    """Map athlete_id (Strava) to user_id (Clerk).

    Args:
        session: Database session
        athlete_id: Strava athlete ID (int)

    Returns:
        User ID (Clerk string) or None if not found
    """
    account = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()

    if account:
        return account[0].user_id
    return None


def save_activity_record(session: Session, record: ActivityRecord) -> Activity:
    """Save a single ActivityRecord to the database.

    LEGACY: Maps from old ActivityRecord (athlete_id) to new Activity (user_id).

    Args:
        session: Database session
        record: ActivityRecord to save (must include athlete_id)

    Returns:
        Saved Activity model instance

    Raises:
        ValueError: If athlete_id cannot be mapped to user_id
    """
    # Map athlete_id to user_id
    user_id = _get_user_id_from_athlete_id(session, record.athlete_id)
    if not user_id:
        raise ValueError(f"Cannot map athlete_id={record.athlete_id} to user_id. Strava account not found.")

    # Extract strava_activity_id from activity_id (format: "strava-12345")
    strava_id = record.activity_id
    if strava_id.startswith("strava-"):
        strava_id = strava_id[7:]  # Remove "strava-" prefix

    # Check if activity already exists
    existing = (
        session.query(Activity)
        .filter_by(
            user_id=user_id,
            strava_activity_id=strava_id,
        )
        .first()
    )

    if existing:
        logger.info(f"[SAVE_ACTIVITIES] Activity {strava_id} already exists for user {user_id}, updating")
        # Update existing record
        existing.start_time = record.start_time
        existing.type = record.sport.capitalize()  # Map sport to type
        existing.duration_seconds = record.duration_sec
        existing.distance_meters = record.distance_m
        existing.elevation_gain_meters = record.elevation_m
        # Store avg_hr in raw_json if available
        if record.avg_hr is not None:
            if existing.raw_json is None:
                existing.raw_json = {}
            existing.raw_json["average_heartrate"] = record.avg_hr
        return existing

    # Create new activity
    logger.info(f"[SAVE_ACTIVITIES] Creating new activity: {strava_id} for user {user_id}")

    # Build raw_json from ActivityRecord
    raw_json: dict | None = None
    if record.avg_hr is not None or record.power is not None:
        raw_json = {}
        if record.avg_hr is not None:
            raw_json["average_heartrate"] = record.avg_hr
        if record.power is not None:
            raw_json.update(record.power)

    activity = Activity(
        user_id=user_id,
        strava_activity_id=strava_id,
        start_time=record.start_time,
        type=record.sport.capitalize(),  # Map sport to type
        duration_seconds=record.duration_sec,
        distance_meters=record.distance_m,
        elevation_gain_meters=record.elevation_m,
        raw_json=raw_json,
    )
    session.add(activity)
    logger.info(f"[SAVE_ACTIVITIES] Added new activity: {strava_id} for user {user_id}")
    return activity


def save_activity_records(session: Session, records: list[ActivityRecord]) -> int:
    """Save multiple ActivityRecords to the database.

    Args:
        session: Database session
        records: List of ActivityRecords to save

    Returns:
        Number of activities saved (including updates)
    """
    if not records:
        logger.info("[SAVE_ACTIVITIES] No activity records to save")
        return 0

    logger.info(f"[SAVE_ACTIVITIES] Saving {len(records)} activity records to database")
    saved_count = 0

    for record in records:
        try:
            save_activity_record(session, record)
            saved_count += 1
        except Exception as e:
            logger.error(f"[SAVE_ACTIVITIES] Error saving activity {record.activity_id}: {e}")
            # Continue with other activities even if one fails
            continue

    session.commit()
    logger.info(f"[SAVE_ACTIVITIES] Successfully saved {saved_count}/{len(records)} activities to database")
    return saved_count
