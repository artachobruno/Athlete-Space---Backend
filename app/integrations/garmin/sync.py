"""DEPRECATED: Ongoing incremental sync for Garmin activities.

This module is deprecated. History fetching via /activities endpoint is disabled.
Use Summary Backfill API and webhooks instead.

All activity data should arrive via webhooks after triggering Summary Backfill.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.db.models import Activity, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import check_garmin_activity_exists, check_strava_duplicate
from app.integrations.garmin.client import get_garmin_client
from app.integrations.garmin.normalize import normalize_garmin_activity
from app.workouts.workout_factory import WorkoutFactory


def sync_garmin_activities(user_id: str) -> dict[str, int | str]:
    """DEPRECATED: Incremental sync for Garmin activities.

    This function is deprecated. History fetching via /activities endpoint is disabled.
    Use Summary Backfill API and webhooks instead.

    Args:
        user_id: User ID to sync

    Returns:
        Sync results with error status
    """
    logger.warning(
        f"[GARMIN_SYNC] DEPRECATED: sync_garmin_activities called for user_id={user_id}. "
        "History fetching is disabled. Use Summary Backfill API and webhooks instead."
    )

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_SYNC] Garmin integration disabled, skipping sync for user_id={user_id}")
        return {"imported_count": 0, "skipped_count": 0, "error_count": 0, "status": "disabled"}

    # Return error - this method should not be used
    return {
        "imported_count": 0,
        "skipped_count": 0,
        "error_count": 1,
        "status": "deprecated",
        "error": "History fetching via /activities endpoint is disabled. Use Summary Backfill API and webhooks instead.",
    }


def _process_activity_for_sync(
    session,
    user_id: str,
    normalized: dict[str, Any],
    is_update: bool = False,
) -> str:
    """Process a normalized Garmin activity for sync.

    Args:
        session: Database session
        user_id: User ID
        normalized: Normalized activity dict
        is_update: True if this is an update event

    Returns:
        "ingested", "updated", "skipped_duplicate", "skipped_strava_duplicate", or "error"
    """
    try:
        external_activity_id = normalized.get("external_activity_id")
        if not external_activity_id:
            logger.warning("[GARMIN_SYNC] Activity missing external_activity_id")
            return "error"

        # Check if Garmin activity already exists
        existing_garmin = check_garmin_activity_exists(session, external_activity_id)

        if existing_garmin:
            if is_update:
                logger.info(f"[GARMIN_SYNC] Updating existing activity: {external_activity_id}")
                _update_activity_metadata(existing_garmin, normalized)
                return "updated"
            logger.debug(f"[GARMIN_SYNC] Activity already exists: {external_activity_id}")
            return "skipped_duplicate"

        # Check for Strava duplicate
        start_time = datetime.fromisoformat(normalized["start_time"].replace("Z", "+00:00"))
        distance_meters = normalized.get("distance_meters")
        existing_strava = check_strava_duplicate(session, user_id, start_time, distance_meters)

        if existing_strava:
            logger.info(
                f"[GARMIN_SYNC] Strava duplicate detected for Garmin activity {external_activity_id}: "
                f"strava_id={existing_strava.source_activity_id}"
            )
            # Link Garmin data to Strava activity
            if existing_strava.metrics and isinstance(existing_strava.metrics, dict):
                existing_strava.metrics["garmin_activity_id"] = external_activity_id
                session.commit()
            return "skipped_strava_duplicate"

        # Create new activity
        activity = Activity(
            user_id=user_id,
            source="garmin",
            source_activity_id=external_activity_id,
            source_provider="garmin",
            external_activity_id=external_activity_id,
            sport=normalized.get("sport", "other"),
            starts_at=start_time,
            ends_at=(
                datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00"))
                if normalized.get("ends_at")
                else None
            ),
            duration_seconds=normalized.get("duration_seconds", 0),
            distance_meters=normalized.get("distance_meters"),
            elevation_gain_meters=normalized.get("elevation_gain_meters"),
            calories=normalized.get("calories"),
            title=normalized.get("title"),
            metrics=normalized.get("metrics", {}),
        )

        session.add(activity)
        session.flush()  # Ensure ID is generated

        # PHASE 3: Enforce workout + execution creation (mandatory invariant)
        # Note: get_or_create_for_activity already creates the execution, so no need to call attach_activity
        workout = WorkoutFactory.get_or_create_for_activity(session, activity)

        logger.debug(f"[GARMIN_SYNC] Stored activity with workout and execution: {external_activity_id}")
    except IntegrityError as e:
        session.rollback()
        logger.debug(f"[GARMIN_SYNC] Duplicate detected during commit (race condition): {e}")
        return "skipped_duplicate"
    except Exception as e:
        logger.exception(
            f"[GARMIN_SYNC] Error processing activity {external_activity_id if 'external_activity_id' in locals() else 'unknown'}: {e}",
            exc_info=True,
        )
        session.rollback()
        return "error"
    else:
        return "ingested"


def _update_activity_metadata(
    activity: Activity,
    normalized: dict[str, Any],
) -> None:
    """Update activity metadata without overwriting user edits.

    Updates:
    - Duration, distance, elevation, calories
    - HR data in metrics
    - Raw JSON in metrics

    Does NOT update:
    - User-edited titles
    - Manual notes

    Args:
        session: Database session
        activity: Existing Activity to update
        normalized: Normalized activity data
    """
    # Update duration if changed
    new_duration = normalized.get("duration_seconds", 0)
    if new_duration > 0:
        activity.duration_seconds = new_duration

    # Update distance, elevation, calories
    if normalized.get("distance_meters") is not None:
        activity.distance_meters = normalized.get("distance_meters")
    if normalized.get("elevation_gain_meters") is not None:
        activity.elevation_gain_meters = normalized.get("elevation_gain_meters")
    if normalized.get("calories") is not None:
        activity.calories = normalized.get("calories")

    # Update metrics (HR, raw_json) - merge, don't overwrite
    if normalized.get("metrics"):
        if not activity.metrics:
            activity.metrics = {}
        if isinstance(activity.metrics, dict):
            # Merge HR data
            if "heart_rate" in normalized["metrics"]:
                if "heart_rate" not in activity.metrics:
                    activity.metrics["heart_rate"] = {}
                activity.metrics["heart_rate"].update(normalized["metrics"]["heart_rate"])

            # Update raw_json
            if "raw_json" in normalized["metrics"]:
                activity.metrics["raw_json"] = normalized["metrics"]["raw_json"]

    # Update ends_at
    if normalized.get("ends_at"):
        activity.ends_at = datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00"))

    logger.debug(f"[GARMIN_SYNC] Updated activity metadata: {activity.id}")
