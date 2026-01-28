"""Garmin webhook endpoints for real-time activity updates.

Rules: always ACK < 1s, no logic inline.
Receives activity events, stores raw payload, enqueues background job.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request

from app.integrations.garmin.webhook_handlers import handle_activities_webhook

router = APIRouter(prefix="/webhooks/garmin", tags=["webhooks", "garmin"])


@router.post("/activities")
async def garmin_webhook_activities(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle Garmin activity webhook events.

    Rules: ACK < 1s, store raw payload, enqueue job, return 200.
    """
    body = await request.body()
    return handle_activities_webhook(body, background_tasks)
