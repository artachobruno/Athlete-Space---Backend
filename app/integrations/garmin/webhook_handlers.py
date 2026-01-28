"""Shared Garmin webhook handling.

Used by both /webhooks/garmin and /integrations/garmin routes.
Rules: always ACK < 1s, no logic inline; store raw payload, enqueue job.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse
from loguru import logger

from app.config.settings import settings
from app.db.models import GarminWebhookEvent
from app.db.session import get_session
from app.integrations.garmin.jobs import process_garmin_activity_event


def handle_activities_webhook(body: bytes, background_tasks: BackgroundTasks) -> JSONResponse:
    """Process Garmin Activities webhook payload.

    Parse JSON, store in garmin_webhook_events, enqueue job, return 200.
    Caller must return the JSONResponse as-is.

    Args:
        body: Raw request body
        background_tasks: FastAPI background tasks

    Returns:
        JSONResponse with status 200
    """
    if not settings.garmin_webhooks_enabled:
        logger.debug("[GARMIN_WEBHOOK] Webhooks disabled, ignoring event")
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "webhooks_disabled"},
        )

    try:
        payload: dict[str, Any] = json.loads(body.decode())
    except Exception as e:
        logger.error(f"[GARMIN_WEBHOOK] Failed to parse webhook payload: {e}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "reason": "invalid_payload"},
        )

    event_type = payload.get("eventType") or payload.get("event_type") or "activity.created"
    logger.info(f"[GARMIN_WEBHOOK] Received event: {event_type}")

    try:
        with get_session() as session:
            webhook_event = GarminWebhookEvent(
                id=str(uuid.uuid4()),
                event_type=event_type,
                payload=payload,
                received_at=datetime.now(timezone.utc),
                status="pending",
            )
            session.add(webhook_event)
            session.commit()
            event_id = webhook_event.id
            logger.debug(f"[GARMIN_WEBHOOK] Stored webhook event: {event_id}")
    except Exception as e:
        logger.error(f"[GARMIN_WEBHOOK] Failed to store webhook event: {e}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "reason": "storage_failed"},
        )

    try:
        background_tasks.add_task(process_garmin_activity_event, event_id)
        logger.debug(f"[GARMIN_WEBHOOK] Enqueued background job for event: {event_id}")
    except Exception as e:
        logger.error(f"[GARMIN_WEBHOOK] Failed to enqueue background job: {e}")

    return JSONResponse(
        status_code=200,
        content={"status": "acknowledged", "event_id": event_id},
    )
