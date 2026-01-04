"""Database helper functions for activity storage and user state management."""

from __future__ import annotations

import time
from datetime import datetime

from loguru import logger

from app.ingestion.save_activities import save_activity_record
from app.integrations.strava.schemas import StravaActivity, map_strava_activity
from app.state.db import get_session
from app.state.models import StravaAuth


def store_activity(
    user_id: int,
    source: str,
    activity_id: str,
    start_time: datetime,
    raw: dict | None = None,
) -> None:
    """Store an activity in the database.

    Args:
        user_id: User/athlete ID (maps to athlete_id in StravaAuth)
        source: Activity source (e.g., "strava")
        activity_id: Unique activity identifier
        start_time: Activity start time
        raw: Raw activity data (currently not stored in Activity model)

    Raises:
        ValueError: If athlete_id cannot be mapped to user_id (StravaAccount not found)
        Exception: Other errors during activity storage
    """
    logger.debug(
        f"[DATA] store_activity called: activity_id={activity_id}, athlete_id={user_id}, "
        f"source={source}, raw_type={type(raw)}, raw_is_none={raw is None}"
    )
    
    with get_session() as session:
        logger.debug(f"[DATA] Database session created for activity {activity_id}")
        
        # Convert raw Strava activity to ActivityRecord if provided
        if raw and source == "strava":
            try:
                # Validate raw data has required fields
                logger.debug(
                    f"[DATA] Validating raw data for activity {activity_id}: "
                    f"type={type(raw)}, is_dict={isinstance(raw, dict)}"
                )
                
                if not isinstance(raw, dict):
                    raise ValueError(f"Raw data must be a dict, got {type(raw)}")
                
                raw_keys = list(raw.keys()) if isinstance(raw, dict) else []
                logger.debug(
                    f"[DATA] Raw data keys for activity {activity_id}: {raw_keys[:20]} "
                    f"(total: {len(raw_keys)})"
                )
                
                if "id" not in raw:
                    raise ValueError(f"Raw data missing 'id' field. Keys: {raw_keys[:10]}")
                
                logger.debug(
                    f"[DATA] Raw data 'id' field: {raw.get('id')}, type: {type(raw.get('id'))}"
                )
                
                logger.debug(f"[DATA] Creating StravaActivity from raw data for activity {activity_id}")
                strava_activity = StravaActivity(**raw)
                logger.debug(
                    f"[DATA] StravaActivity created: id={strava_activity.id}, "
                    f"type={strava_activity.type}, raw_present={strava_activity.raw is not None}"
                )
                
                logger.debug(f"[DATA] Mapping StravaActivity to ActivityRecord for activity {activity_id}")
                record = map_strava_activity(strava_activity, athlete_id=user_id)
                logger.debug(
                    f"[DATA] ActivityRecord created: activity_id={record.activity_id}, "
                    f"athlete_id={record.athlete_id}, sport={record.sport}, "
                    f"duration_sec={record.duration_sec}, distance_m={record.distance_m}"
                )
                
                # Pass full raw data to save_activity_record for storage in raw_json
                logger.debug(
                    f"[DATA] Calling save_activity_record with raw_json size: "
                    f"{len(str(raw)) if raw else 0} chars, keys: {len(raw_keys) if raw else 0}"
                )
                save_activity_record(session, record, raw_json=raw)
                logger.debug(f"[DATA] save_activity_record completed for activity {activity_id}")
                
                # Note: session.commit() is handled by get_session() context manager
                logger.debug(f"[DATA] About to commit session for activity {activity_id}")
                logger.info(f"[DATA] Stored activity {activity_id} for athlete_id={user_id} (start_time={start_time})")
            except ValueError as e:
                # ValueError from save_activity_record means StravaAccount lookup failed
                logger.error(
                    f"[DATA] Cannot store activity {activity_id} for athlete_id={user_id}: "
                    f"StravaAccount not found. Error: {e}"
                )
                raise
            except KeyError as e:
                # KeyError means missing required field in raw data
                logger.error(
                    f"[DATA] KeyError storing activity {activity_id} for athlete_id={user_id}: {e}. "
                    f"Raw data keys: {list(raw.keys())[:20] if isinstance(raw, dict) else 'N/A'}",
                    exc_info=True
                )
                raise
            except Exception as e:
                logger.error(
                    f"[DATA] Error storing activity {activity_id} for athlete_id={user_id}: {e}. "
                    f"Error type: {type(e).__name__}",
                    exc_info=True
                )
                raise
        else:
            # Fallback path should not be used for Strava activities (raw data required)
            logger.warning(
                f"[DATA] Attempted to store activity {activity_id} without raw data. "
                f"This fallback is not supported. Raw data is required for Strava activities."
            )
            raise ValueError(f"Cannot store activity {activity_id} without raw data. Raw Strava data is required.")


def update_last_ingested_at(user_id: int, timestamp: int) -> None:
    """Update the last ingested timestamp for a user.

    Args:
        user_id: User/athlete ID (maps to athlete_id in StravaAuth)
        timestamp: UNIX timestamp of last ingested activity
    """
    with get_session() as session:
        user = session.query(StravaAuth).filter_by(athlete_id=user_id).first()
        if user:
            # Note: StravaAuth model needs last_ingested_at field
            if hasattr(user, "last_ingested_at"):
                user.last_ingested_at = timestamp
                logger.debug(f"Updated last_ingested_at for user {user_id}")
            else:
                logger.warning(f"StravaAuth model missing last_ingested_at field for user {user_id}")


def mark_backfill_done(user_id: int) -> None:
    """Mark backfill as complete for a user.

    Args:
        user_id: User/athlete ID (maps to athlete_id in StravaAuth)
    """
    with get_session() as session:
        user = session.query(StravaAuth).filter_by(athlete_id=user_id).first()
        if user:
            # Note: StravaAuth model needs backfill_done field
            if hasattr(user, "backfill_done"):
                user.backfill_done = True
                logger.debug(f"Marked backfill as done for user {user_id}")
            else:
                logger.warning(f"StravaAuth model missing backfill_done field for user {user_id}")


def update_backfill_page(user_id: int, page: int) -> None:
    """Update the backfill page number for a user.

    Args:
        user_id: User/athlete ID (maps to athlete_id in StravaAuth)
        page: Current backfill page number
    """
    with get_session() as session:
        user = session.query(StravaAuth).filter_by(athlete_id=user_id).first()
        if user:
            # Note: StravaAuth model needs backfill_page field
            if hasattr(user, "backfill_page"):
                user.backfill_page = page
                if hasattr(user, "backfill_updated_at"):
                    user.backfill_updated_at = int(time.time())
                logger.debug(f"Updated backfill_page to {page} for user {user_id}")
            else:
                logger.warning(f"StravaAuth model missing backfill_page field for user {user_id}")
