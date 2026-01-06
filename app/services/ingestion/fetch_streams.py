"""Utility functions to fetch and save Strava streams data for activities.

Streams data includes time-series information like GPS coordinates, heart rate,
power, cadence, speed, etc. This can be used for:
- Route visualization (GPS coordinates)
- Workout compliance analysis (HR zones, power zones)
- Performance graphs (pace, speed over time)
- Detailed activity analysis

Note: Fetching streams uses additional API quota, so this should be done
selectively (e.g., only for recent activities or on-demand).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.services.integrations.strava.client import StravaClient


def fetch_and_save_streams(
    session: Session,
    client: StravaClient,
    activity: Activity,
) -> bool:
    """Fetch and save streams data for an activity.

    Args:
        session: Database session
        client: StravaClient instance with valid access token
        activity: Activity model instance

    Returns:
        True if streams were successfully fetched and saved, False otherwise
    """
    if activity.source != "strava":
        logger.debug(f"[FETCH_STREAMS] Activity {activity.id} is not from Strava, skipping")
        return False

    if activity.streams_data is not None:
        logger.debug(f"[FETCH_STREAMS] Activity {activity.id} already has streams data, skipping")
        return False

    try:
        strava_activity_id = int(activity.strava_activity_id)
    except (ValueError, TypeError):
        logger.warning(f"[FETCH_STREAMS] Invalid strava_activity_id: {activity.strava_activity_id}")
        return False

    logger.info(f"[FETCH_STREAMS] Fetching streams for activity {strava_activity_id}")
    try:
        streams = client.fetch_activity_streams(activity_id=strava_activity_id)
        if streams is None:
            logger.debug(f"[FETCH_STREAMS] No streams available for activity {strava_activity_id}")
            return False

        # Update activity with streams data
        activity.streams_data = streams
        session.add(activity)
        session.commit()

        data_points = len(streams.get("time", []))
        logger.info(
            f"[FETCH_STREAMS] Successfully saved streams for activity {strava_activity_id}: "
            f"{len(streams)} stream types, {data_points} data points"
        )
    except Exception as e:
        logger.error(f"[FETCH_STREAMS] Error fetching streams for activity {strava_activity_id}: {e}")
        session.rollback()
        return False
    else:
        return True


def fetch_streams_for_recent_activities(
    session: Session,
    client: StravaClient,
    user_id: str,
    days: int = 7,
    limit: int = 50,
) -> int:
    """Fetch streams for recent activities that don't have streams data yet.

    Args:
        session: Database session
        client: StravaClient instance with valid access token
        user_id: User ID to filter activities
        days: Number of days to look back (default: 7)
        limit: Maximum number of activities to process (default: 50)

    Returns:
        Number of activities for which streams were successfully fetched
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    activities = (
        session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.source == "strava",
                Activity.start_time >= cutoff_date,
                Activity.streams_data.is_(None),
            )
            .order_by(Activity.start_time.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    if not activities:
        logger.info(f"[FETCH_STREAMS] No activities found without streams data for user {user_id}")
        return 0

    logger.info(f"[FETCH_STREAMS] Found {len(activities)} activities without streams data for user {user_id}")

    success_count = 0
    for activity in activities:
        if fetch_and_save_streams(session, client, activity):
            success_count += 1

    logger.info(f"[FETCH_STREAMS] Successfully fetched streams for {success_count}/{len(activities)} activities")
    return success_count
