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
    """
    with get_session() as session:
        # Convert raw Strava activity to ActivityRecord if provided
        if raw and source == "strava":
            try:
                strava_activity = StravaActivity(**raw)
                record = map_strava_activity(strava_activity, athlete_id=user_id)
                save_activity_record(session, record)
                logger.info(f"[DATA] Stored activity {activity_id} for user {user_id} (start_time={start_time})")
            except Exception as e:
                logger.error(f"[DATA] Error storing activity {activity_id} for user {user_id}: {e}")
                raise
        else:
            # Fallback: create minimal activity record
            # Note: This is a simplified version - full implementation would
            # require more fields from the raw data
            existing = session.query(Activity).filter_by(athlete_id=user_id, activity_id=activity_id, source=source).first()
            if not existing:
                activity = Activity(
                    athlete_id=user_id,
                    activity_id=activity_id,
                    source=source,
                    sport="unknown",  # Would need to extract from raw data
                    start_time=start_time,
                    duration_s=0,  # Would need to extract from raw data
                    distance_m=0.0,  # Would need to extract from raw data
                    elevation_m=0.0,  # Would need to extract from raw data
                    avg_hr=None,
                )
                session.add(activity)
                logger.debug(f"Stored minimal activity {activity_id} for user {user_id}")


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
