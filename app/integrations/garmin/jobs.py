"""Background jobs for processing Garmin integration events."""

from __future__ import annotations

from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import select

from app.config.settings import settings
from app.db.models import GarminWebhookEvent, UserIntegration
from app.db.session import get_session
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

        # Skip if already processed
        if event_obj.status == "processed":
            logger.debug(f"[GARMIN_JOB] Event already processed: {event_id}")
            return

        try:
            # Extract provider_user_id from payload (adjust based on actual Garmin format)
            payload = event_obj.payload
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

            # Fetch activity payload (mock for now - replace with actual Garmin API call)
            logger.info(f"[GARMIN_JOB] Fetching activity data for event: {event_id} (mock)")
            activity_payload = payload  # Use webhook payload as mock

            # Normalize activity
            try:
                normalized_activity = normalize_garmin_activity(activity_payload)
                logger.info(f"[GARMIN_JOB] Normalized activity: {normalized_activity.get('sport', 'unknown')}")
            except Exception as e:
                logger.error(f"[GARMIN_JOB] Normalization failed: {e}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            # TODO: Store normalized activity (call ingestion interface)
            # For now, just log
            logger.info(f"[GARMIN_JOB] Activity normalized (stub ingestion): {normalized_activity}")

            # Mark event as processed
            event_obj.status = "processed"
            event_obj.processed_at = datetime.now(timezone.utc)
            session.commit()

            logger.info(f"[GARMIN_JOB] Successfully processed event: {event_id}")

        except Exception as e:
            logger.exception(f"[GARMIN_JOB] Error processing event {event_id}: {e}")
            event_obj.status = "failed"
            event_obj.processed_at = datetime.now(timezone.utc)
            session.commit()
