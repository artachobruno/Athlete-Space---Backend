"""Save activity records to the database.

LEGACY: This module maps from old ActivityRecord format to new Activity model.
The new ingestion system (ingestion_strava.py, background_sync.py) directly uses the new Activity schema.
"""

from __future__ import annotations

import uuid

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


def save_activity_record(session: Session, record: ActivityRecord, raw_json: dict | None = None) -> Activity:
    """Save a single ActivityRecord to the database.

    LEGACY: Maps from old ActivityRecord (athlete_id) to new Activity (user_id).

    Args:
        session: Database session
        record: ActivityRecord to save (must include athlete_id)
        raw_json: Full raw JSON data from Strava API (optional, stored as-is)

    Returns:
        Saved Activity model instance

    Raises:
        ValueError: If athlete_id cannot be mapped to user_id
    """
    logger.debug(
        f"[SAVE_ACTIVITIES] save_activity_record called: activity_id={record.activity_id}, "
        f"athlete_id={record.athlete_id}, raw_json_present={raw_json is not None}, "
        f"raw_json_type={type(raw_json) if raw_json else None}"
    )
    
    # Map athlete_id to user_id
    logger.debug(f"[SAVE_ACTIVITIES] Mapping athlete_id={record.athlete_id} to user_id")
    user_id = _get_user_id_from_athlete_id(session, record.athlete_id)
    if not user_id:
        raise ValueError(f"Cannot map athlete_id={record.athlete_id} to user_id. Strava account not found.")
    logger.debug(f"[SAVE_ACTIVITIES] Mapped athlete_id={record.athlete_id} to user_id={user_id}")

    # Extract strava_activity_id from activity_id (format: "strava-12345")
    strava_id = record.activity_id
    if strava_id.startswith("strava-"):
        strava_id = strava_id[7:]  # Remove "strava-" prefix
    logger.debug(f"[SAVE_ACTIVITIES] Extracted strava_id={strava_id} from activity_id={record.activity_id}")

    # Check if activity already exists
    logger.debug(
        f"[SAVE_ACTIVITIES] Checking for existing activity: user_id={user_id}, "
        f"strava_activity_id={strava_id}"
    )
    existing = (
        session.query(Activity)
        .filter_by(
            user_id=user_id,
            strava_activity_id=strava_id,
        )
        .first()
    )
    logger.debug(f"[SAVE_ACTIVITIES] Existing activity check result: {existing is not None}")

    if existing:
        logger.info(f"[SAVE_ACTIVITIES] Activity {strava_id} already exists for user {user_id}, updating")
        # Update existing record
        existing.start_time = record.start_time
        existing.type = record.sport.capitalize()  # Map sport to type
        existing.duration_seconds = record.duration_sec
        existing.distance_meters = record.distance_m
        existing.elevation_gain_meters = record.elevation_m
        # Update raw_json if provided
        if raw_json is not None:
            existing.raw_json = raw_json
        elif record.avg_hr is not None:
            # Fallback: update raw_json with avg_hr if raw_json not provided
            if existing.raw_json is None:
                existing.raw_json = {}
            existing.raw_json["average_heartrate"] = record.avg_hr
        return existing

    # Create new activity
    logger.info(f"[SAVE_ACTIVITIES] Creating new activity: {strava_id} for user {user_id}")

    # Use provided raw_json, or build minimal dict if not provided (legacy fallback)
    logger.debug(
        f"[SAVE_ACTIVITIES] Processing raw_json: provided={raw_json is not None}, "
        f"type={type(raw_json)}, is_dict={isinstance(raw_json, dict) if raw_json else False}"
    )
    
    if raw_json is None:
        logger.debug("[SAVE_ACTIVITIES] raw_json not provided, building minimal dict")
        raw_json = {}
        if record.avg_hr is not None:
            raw_json["average_heartrate"] = record.avg_hr
        if record.power is not None:
            raw_json.update(record.power)
        # If no data, set to None
        if not raw_json:
            raw_json = None
            logger.debug("[SAVE_ACTIVITIES] No raw_json data, setting to None")
        else:
            logger.debug(f"[SAVE_ACTIVITIES] Built minimal raw_json with keys: {list(raw_json.keys())}")
    else:
        raw_json_keys = list(raw_json.keys()) if isinstance(raw_json, dict) else []
        logger.debug(
            f"[SAVE_ACTIVITIES] Using provided raw_json with {len(raw_json_keys)} keys: "
            f"{raw_json_keys[:10]}"
        )
        if "id" in raw_json:
            logger.debug(f"[SAVE_ACTIVITIES] raw_json contains 'id' field: {raw_json.get('id')}")

    # Let the model's default handle id generation to avoid conflicts
    logger.debug(
        f"[SAVE_ACTIVITIES] Creating Activity object: user_id={user_id}, "
        f"strava_activity_id={strava_id}, type={record.sport.capitalize()}, "
        f"start_time={record.start_time}, duration_seconds={record.duration_sec}, "
        f"distance_meters={record.distance_m}, elevation_gain_meters={record.elevation_m}, "
        f"raw_json_type={type(raw_json)}, raw_json_keys={len(raw_json) if isinstance(raw_json, dict) else 0}"
    )
    
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
    
    # Check if id was generated
    activity_id = getattr(activity, 'id', None)
    logger.debug(
        f"[SAVE_ACTIVITIES] Activity object created: id={activity_id}, "
        f"id_type={type(activity_id)}, user_id={activity.user_id}, "
        f"strava_activity_id={activity.strava_activity_id}, "
        f"raw_json_present={activity.raw_json is not None}, "
        f"raw_json_type={type(activity.raw_json) if activity.raw_json else None}"
    )
    
    if activity.raw_json and isinstance(activity.raw_json, dict):
        raw_json_keys = list(activity.raw_json.keys())
        logger.debug(
            f"[SAVE_ACTIVITIES] raw_json has {len(raw_json_keys)} keys, "
            f"has 'id': {'id' in raw_json_keys}, sample keys: {raw_json_keys[:10]}"
        )
        if 'id' in activity.raw_json:
            logger.debug(f"[SAVE_ACTIVITIES] raw_json['id'] = {activity.raw_json.get('id')}, type: {type(activity.raw_json.get('id'))}")
    
    logger.debug(f"[SAVE_ACTIVITIES] Adding activity to session: {strava_id}")
    session.add(activity)
    logger.debug(f"[SAVE_ACTIVITIES] Activity added to session, session.dirty: {len(session.dirty)}, session.new: {len(session.new)}")
    
    # Verify activity is in session
    if activity in session.new:
        logger.debug(f"[SAVE_ACTIVITIES] Activity confirmed in session.new")
    else:
        logger.warning(f"[SAVE_ACTIVITIES] Activity NOT in session.new after add!")
    
    # Note: session.commit() is handled by the get_session() context manager
    # The commit will happen when the context manager exits, and any errors will be raised then
    
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
