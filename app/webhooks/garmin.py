"""Garmin webhook endpoints for real-time activity updates.

Rules: always ACK < 1s, no logic inline.
Receives activity events, stores raw payload, enqueues background job.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

from app.config.settings import settings
from app.db.models import GarminWebhookEvent
from app.db.session import get_session
from app.integrations.garmin.jobs import process_garmin_activity_event

router = APIRouter(prefix="/webhooks/garmin", tags=["webhooks", "garmin"])


@router.post("/activities")
async def garmin_webhook_activities(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Handle Garmin activity webhook events.

    Rules:
    - Always ACK < 1s
    - No logic inline
    - Parse payload
    - Store raw payload
    - Enqueue background job
    - Return 200 OK

    Args:
        request: FastAPI request object
        background_tasks: FastAPI background tasks for async processing

    Returns:
        200 OK response immediately
    """
    if not settings.garmin_webhooks_enabled:
        logger.debug("[GARMIN_WEBHOOK] Webhooks disabled, ignoring event")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "webhooks_disabled"},
        )

    # Read request body
    try:
        body = await request.body()
        payload: dict[str, Any] = json.loads(body.decode())
    except Exception as e:
        logger.error(f"[GARMIN_WEBHOOK] Failed to parse webhook payload: {e}")
        # Still return 200 OK to avoid webhook retries
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "error", "reason": "invalid_payload"},
        )

    # Extract event type (adjust based on actual Garmin webhook format)
    event_type = payload.get("eventType") or payload.get("event_type") or "activity.created"
    logger.info(f"[GARMIN_WEBHOOK] Received event: {event_type}")

    # Store raw payload in database (fast, no processing)
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
        # Still return 200 OK
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "error", "reason": "storage_failed"},
        )

    # Enqueue background job (non-blocking)
    try:
        background_tasks.add_task(process_garmin_activity_event, event_id)
        logger.debug(f"[GARMIN_WEBHOOK] Enqueued background job for event: {event_id}")
    except Exception as e:
        logger.error(f"[GARMIN_WEBHOOK] Failed to enqueue background job: {e}")
        # Still return 200 OK - event is stored, can be processed later

    # Always return 200 OK immediately (< 1s)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "acknowledged", "event_id": event_id},
    )
