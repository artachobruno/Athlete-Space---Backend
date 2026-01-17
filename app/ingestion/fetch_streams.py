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
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Activity, UserSettings
from app.integrations.strava.client import StravaClient
from app.metrics.effort_service import compute_activity_effort
from app.metrics.load_computation import AthleteThresholds, compute_activity_tss


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

    if activity.source != "strava" or not activity.source_activity_id:
        logger.warning("[FETCH_STREAMS] Activity is not from Strava or missing source_activity_id")
        return False

    try:
        strava_activity_id = int(activity.source_activity_id)
    except (ValueError, TypeError):
        logger.warning(f"[FETCH_STREAMS] Invalid source_activity_id: {activity.source_activity_id}")
        return False

    logger.info(f"[FETCH_STREAMS] Fetching streams for activity {strava_activity_id}")
    try:
        streams = client.fetch_activity_streams(activity_id=strava_activity_id)
        if streams is None:
            logger.debug(f"[FETCH_STREAMS] No streams available for activity {strava_activity_id}")
            return False

        # Validate streams is a dict
        if not isinstance(streams, dict):
            logger.error(
                f"[FETCH_STREAMS] Invalid streams format for activity {strava_activity_id}: expected dict, got {type(streams).__name__}"
            )
            return False

        # Update activity with streams data (store in metrics JSONB, not the read-only property)
        if activity.metrics is None:
            activity.metrics = {}
        activity.metrics["streams_data"] = streams
        # Mark JSONB column as modified so SQLAlchemy detects the change
        flag_modified(activity, "metrics")
        session.add(activity)

        # TSS MUST be computed after streams_data is present.
        # Compute effort metrics and TSS after streams are saved
        try:
            user_settings = session.query(UserSettings).filter_by(user_id=activity.user_id).first()

            # Compute effort metrics
            normalized_effort, effort_source, intensity_factor = compute_activity_effort(activity, user_settings)
            activity.normalized_power = normalized_effort
            activity.effort_source = effort_source
            activity.intensity_factor = intensity_factor
            if normalized_effort is not None:
                logger.debug(
                    f"[FETCH_STREAMS] Computed effort for activity {strava_activity_id}: "
                    f"normalized_effort={normalized_effort}, source={effort_source}, IF={intensity_factor}"
                )

            # Compute and persist TSS
            athlete_thresholds = _build_athlete_thresholds(user_settings)
            tss = compute_activity_tss(activity, athlete_thresholds)
            activity.tss = tss
            activity.tss_version = "v2"

            logger.debug(
                f"[FETCH_STREAMS] Computed TSS for activity {strava_activity_id}: "
                f"tss={tss}, version=v2"
            )
        except Exception as e:
            logger.warning(f"[FETCH_STREAMS] Failed to compute effort/TSS for activity {strava_activity_id}: {e}")

        session.commit()

        # Count data points correctly (streams format: {"time": {"data": [...]}, ...})
        data_points = 0
        if streams and "time" in streams:
            time_stream = streams["time"]
            if isinstance(time_stream, dict) and "data" in time_stream:
                data_points = len(time_stream["data"])
            elif isinstance(time_stream, list):
                data_points = len(time_stream)

        logger.info(
            f"[FETCH_STREAMS] Successfully saved streams for activity {strava_activity_id}: "
            f"{len(streams)} stream types, {data_points} data points"
        )
    except Exception:
        logger.exception(
            f"[FETCH_STREAMS] Error fetching streams for activity {strava_activity_id}"
        )
        session.rollback()
        # Re-raise the exception so the API endpoint can handle it properly
        raise
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
                Activity.starts_at >= cutoff_date,
                Activity.streams_data.is_(None),
            )
            .order_by(Activity.starts_at.desc())
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


def _build_athlete_thresholds(user_settings: UserSettings | None) -> AthleteThresholds | None:
    """Build AthleteThresholds from UserSettings.

    Args:
        user_settings: User settings with threshold configuration

    Returns:
        AthleteThresholds instance or None if no user settings
    """
    if not user_settings:
        return None

    return AthleteThresholds(
        ftp_watts=user_settings.ftp_watts,
        threshold_pace_ms=user_settings.threshold_pace_ms,
    )
