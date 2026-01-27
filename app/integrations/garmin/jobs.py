"""Background jobs for processing Garmin integration events.

Webhook-driven sync: ACK fast, process async.
Fetches activity summaries only (no samples).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.db.models import Activity, GarminWebhookEvent, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import check_garmin_activity_exists, check_strava_duplicate
from app.integrations.garmin.client import get_garmin_client
from app.integrations.garmin.normalize import normalize_garmin_activity


def process_garmin_activity_event(event_id: str) -> None:
    """Process a Garmin activity webhook event.

    Background job skeleton:
    - Lookup integration via provider_user_id
    - Fetch activity payload (mock for now)
    - Call normalization layer
    - Mark webhook processed

    Args:
        event_id: Webhook event ID to process
    """
    logger.info(f"[GARMIN_JOB] Processing webhook event: {event_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_JOB] Garmin integration disabled, skipping event: {event_id}")
        return

    with get_session() as session:
        # Fetch webhook event
        event = session.execute(
            select(GarminWebhookEvent).where(GarminWebhookEvent.id == event_id)
        ).first()

        if not event:
            logger.error(f"[GARMIN_JOB] Webhook event not found: {event_id}")
            return

        event_obj = event[0]

        # Webhook replay safety: Skip if already processed
        # Check by event_id OR (external_activity_id + event_type) for idempotency
        if event_obj.status == "processed":
            logger.debug(f"[GARMIN_JOB] Event already processed: {event_id}")
            return

        # Extract payload for idempotency checks
        payload = event_obj.payload

        # Additional idempotency check: if we've already processed this activity for this event type
        activity_id = payload.get("activityId") or payload.get("activity_id") or payload.get("object_id")
        if activity_id:
            # Check if activity already exists and was processed for this event type
            existing_activity = check_garmin_activity_exists(session, str(activity_id))
            if existing_activity and event_obj.event_type == "activity.created":
                logger.info(
                    f"[GARMIN_JOB] Activity {activity_id} already exists for create event {event_id}, "
                    "marking as processed (idempotent)"
                )
                event_obj.status = "processed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

        try:
            # Extract provider_user_id from payload
            provider_user_id = payload.get("userId") or payload.get("user_id") or payload.get("ownerId")

            if not provider_user_id:
                logger.warning(f"[GARMIN_JOB] No provider_user_id in payload for event: {event_id}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            # Lookup integration
            integration = session.execute(
                select(UserIntegration).where(
                    UserIntegration.provider == "garmin",
                    UserIntegration.provider_user_id == str(provider_user_id),
                    UserIntegration.revoked_at.is_(None),  # Not revoked
                )
            ).first()

            if not integration:
                logger.warning(
                    f"[GARMIN_JOB] No active integration found for provider_user_id={provider_user_id}, event: {event_id}"
                )
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            integration_obj = integration[0]
            user_id = integration_obj.user_id

            # Extract activity ID from webhook payload
            activity_id = payload.get("activityId") or payload.get("activity_id") or payload.get("object_id")

            if not activity_id:
                logger.warning(f"[GARMIN_JOB] No activity_id in payload for event: {event_id}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            # Fetch activity summary from Garmin API (no samples)
            try:
                client = get_garmin_client(user_id)
                activity_payload = client.fetch_activity_detail(str(activity_id))
                logger.info(f"[GARMIN_JOB] Fetched activity summary: {activity_id}")
            except Exception as e:
                logger.error(f"[GARMIN_JOB] Failed to fetch activity from Garmin API: {e}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            # Normalize activity
            try:
                normalized = normalize_garmin_activity(activity_payload)
                logger.debug(f"[GARMIN_JOB] Normalized activity: {normalized.get('sport', 'unknown')}")
            except Exception as e:
                logger.error(f"[GARMIN_JOB] Normalization failed: {e}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            # Process activity (deduplicate, store)
            result = _process_activity_for_webhook(
                session=session,
                user_id=user_id,
                normalized=normalized,
                is_update=event_obj.event_type == "activity.updated" or payload.get("eventType") == "activity.updated",
            )

            if result == "error":
                event_obj.status = "failed"
            else:
                event_obj.status = "processed"

            event_obj.processed_at = datetime.now(timezone.utc)
            session.commit()

            logger.info(f"[GARMIN_JOB] Successfully processed event: {event_id}, result: {result}")

        except Exception as e:
            logger.exception(f"[GARMIN_JOB] Error processing event {event_id}: {e}")
            event_obj.status = "failed"
            event_obj.processed_at = datetime.now(timezone.utc)
            session.commit()


def _process_activity_for_webhook(
    session,
    user_id: str,
    normalized: dict[str, Any],
    is_update: bool = False,
) -> str:
    """Process a normalized Garmin activity for sync (webhook or incremental).

    Args:
        session: Database session
        user_id: User ID
        normalized: Normalized activity dict
        is_update: True if this is an update event (vs create)

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
                # Update existing activity metadata (don't overwrite user edits)
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
        session.flush()

        # TODO: Create workout and execution (like Strava sync)
        # Update last_sync_at on integration
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
            )
        ).first()
        if integration:
            integration[0].last_sync_at = datetime.now(timezone.utc)

        logger.debug(f"[GARMIN_SYNC] Stored activity: {external_activity_id}")
    except IntegrityError:
        # Race condition: activity was inserted between check and commit
        session.rollback()
        logger.debug("[GARMIN_SYNC] Duplicate detected during commit (race condition)")
        return "skipped_duplicate"
    except Exception as e:
        logger.exception(f"[GARMIN_SYNC] Error processing activity: {e}")
        session.rollback()
        return "error"
    else:
        return "ingested"


def _update_activity_metadata(activity: Activity, normalized: dict[str, Any]) -> None:
    """Update activity metadata without overwriting user edits.

    Updates:
    - Duration, distance, elevation, calories
    - HR data in metrics
    - Raw JSON in metrics

    Does NOT update:
    - User-edited titles
    - Manual notes

    Args:
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
