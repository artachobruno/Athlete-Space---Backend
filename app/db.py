"""Database helper functions for activity storage and user state management."""

from __future__ import annotations

import time
from datetime import datetime

from loguru import logger

from app.ingestion.save_activities import save_activity_record
from app.integrations.strava.schemas import StravaActivity, map_strava_activity
from app.state.db import get_session
from app.state.models import Activity, StravaAuth


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
    with get_session() as session:
        # Convert raw Strava activity to ActivityRecord if provided
        if raw and source == "strava":
            try:
                strava_activity = StravaActivity(**raw)
                record = map_strava_activity(strava_activity, athlete_id=user_id)
                save_activity_record(session, record)
                session.commit()
                logger.info(f"[DATA] Stored activity {activity_id} for athlete_id={user_id} (start_time={start_time})")
            except ValueError as e:
                # ValueError from save_activity_record means StravaAccount lookup failed
                logger.error(
                    f"[DATA] Cannot store activity {activity_id} for athlete_id={user_id}: "
                    f"StravaAccount not found. Error: {e}"
                )
                raise
            except Exception as e:
                logger.error(f"[DATA] Error storing activity {activity_id} for athlete_id={user_id}: {e}", exc_info=True)
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
