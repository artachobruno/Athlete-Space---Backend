"""Background jobs for processing Garmin integration events.

Webhook-driven: ACK fast, process async. Ingest from payload only — no fetch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config.settings import settings
from app.db.models import GarminWebhookEvent, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import check_garmin_activity_exists
from app.integrations.garmin.ingest import ingest_activity_summary


def process_garmin_activity_event(event_id: str) -> None:
    """Process a Garmin activity webhook event.

    Ingest from webhook payload only. No fetch. Dedupe, normalize, store.
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
            integration_obj.garmin_last_webhook_received_at = datetime.now(timezone.utc)

            activity_id = (
                payload.get("activityId")
                or payload.get("activity_id")
                or payload.get("object_id")
            )
            if not activity_id:
                logger.warning(f"[GARMIN_JOB] No activity_id in payload for event: {event_id}")
                event_obj.status = "failed"
                event_obj.processed_at = datetime.now(timezone.utc)
                session.commit()
                return

            is_update = (
                event_obj.event_type == "activity.updated"
                or payload.get("eventType") == "activity.updated"
            )
            result = ingest_activity_summary(
                session=session,
                user_id=user_id,
                summary=payload,
                is_update=is_update,
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


def check_and_mark_history_complete() -> None:
    """Check and mark Garmin history as complete using heuristic.

    Heuristic: If backfill was requested and no webhook received in last 6 hours,
    mark history as complete.

    This should be called periodically (e.g., via cron job or scheduled task).
    """
    logger.info("[GARMIN_HISTORY] Checking history completion status")

    with get_session() as session:
        # Find all Garmin integrations with pending history
        integrations = session.execute(
            select(UserIntegration).where(
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
                UserIntegration.garmin_history_requested_at.is_not(None),
                UserIntegration.garmin_history_complete.is_(False),
            )
        ).all()

        if not integrations:
            logger.debug("[GARMIN_HISTORY] No pending history backfills found")
            return

        now = datetime.now(timezone.utc)
        marked_complete = 0

        for integration_tuple in integrations:
            integration = integration_tuple[0]

            # Heuristic: If no webhook received in last 6 hours, mark complete
            if integration.garmin_last_webhook_received_at:
                time_since_webhook = now - integration.garmin_last_webhook_received_at
                if time_since_webhook > timedelta(hours=6):
                    integration.garmin_history_complete = True
                    marked_complete += 1
                    logger.info(
                        f"[GARMIN_HISTORY] Marked history complete for user_id={integration.user_id}: "
                        f"no webhook in {time_since_webhook}"
                    )
            elif integration.garmin_history_requested_at:
                # If backfill was requested but no webhook ever received, check time since request
                time_since_request = now - integration.garmin_history_requested_at
                if time_since_request > timedelta(hours=6):
                    integration.garmin_history_complete = True
                    marked_complete += 1
                    logger.info(
                        f"[GARMIN_HISTORY] Marked history complete for user_id={integration.user_id}: "
                        f"no webhook received since request ({time_since_request})"
                    )

        if marked_complete > 0:
            session.commit()
            logger.info(f"[GARMIN_HISTORY] Marked {marked_complete} integration(s) as complete")
        else:
            logger.debug("[GARMIN_HISTORY] No integrations ready to mark as complete")
