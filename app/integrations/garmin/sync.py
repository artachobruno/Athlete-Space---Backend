"""Ongoing incremental sync for Garmin activities.

Webhook-driven sync with fallback incremental polling.
Fetches activity summaries only (no samples).
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
    """Incremental sync for Garmin activities (webhook fallback).

    Fetches activities since last_sync_at.
    Only fetches summaries (no samples).

    Args:
        user_id: User ID to sync

    Returns:
        Sync results: {imported_count, skipped_count, error_count}
    """
    logger.info(f"[GARMIN_SYNC] Starting incremental sync for user_id={user_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_SYNC] Garmin integration disabled, skipping sync for user_id={user_id}")
        return {"imported_count": 0, "skipped_count": 0, "error_count": 0, "status": "disabled"}

    with get_session() as session:
        # Get user's Garmin integration
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
            )
        ).first()

        if not integration:
            logger.warning(f"[GARMIN_SYNC] No active Garmin integration for user_id={user_id}")
            return {"imported_count": 0, "skipped_count": 0, "error_count": 0, "status": "no_integration"}

        integration_obj = integration[0]

        # Determine sync window: since last_sync_at, or last 7 days if never synced
        if integration_obj.last_sync_at:
            from_date = integration_obj.last_sync_at
        else:
            from_date = datetime.now(timezone.utc) - timedelta(days=7)

        to_date = datetime.now(timezone.utc)

        logger.info(f"[GARMIN_SYNC] Syncing activities from {from_date.date()} to {to_date.date()}")

        try:
            client = get_garmin_client(user_id)
        except ValueError as e:
            logger.error(f"[GARMIN_SYNC] Failed to get Garmin client: {e}")
            return {"imported_count": 0, "skipped_count": 0, "error_count": 0, "status": "client_error", "error": str(e)}

        imported_count = 0
        skipped_count = 0
        error_count = 0

        try:
            # Fetch activities page by page (memory-efficient)
            for activities_page in client.yield_activity_summaries(
                start_date=from_date,
                end_date=to_date,
                per_page=100,
                max_pages=10,  # Limit incremental sync to 10 pages (1000 activities)
                sleep_seconds=0.5,
            ):
                # Process each activity
                for activity_item in activities_page:
                    activity_payload: dict[str, Any] = activity_item
                    try:
                        normalized = normalize_garmin_activity(activity_payload)
                        result = _process_activity_for_sync(
                            session=session,
                            user_id=user_id,
                            normalized=normalized,
                            is_update=False,
                        )
                    except Exception as e:
                        logger.exception(f"[GARMIN_SYNC] Error normalizing activity: {e}")
                        result = "error"

                    if result == "ingested":
                        imported_count += 1
                    elif result in {"skipped_duplicate", "skipped_strava_duplicate"}:
                        skipped_count += 1
                    else:
                        error_count += 1

                # Commit after each page
                session.commit()

        except Exception as e:
            logger.exception(f"[GARMIN_SYNC] Sync failed: {e}")
            error_count += 1

        # Update last_sync_at
        integration_obj.last_sync_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(
            f"[GARMIN_SYNC] Sync complete for user_id={user_id}: "
            f"imported={imported_count}, skipped={skipped_count}, errors={error_count}"
        )

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "status": "completed",
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
