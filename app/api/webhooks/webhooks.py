"""Strava webhook endpoints for real-time activity updates.

Step 5: Webhook handler for Strava Push Subscriptions.
Receives activity creation events and triggers background sync.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select

from app.config.settings import settings
from app.db.models import StravaAccount
from app.db.session import get_session
from app.services.ingestion.background_sync import sync_user_activities

router = APIRouter(prefix="/webhooks/strava", tags=["webhooks", "strava"])


def _verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify Strava webhook signature.

    Args:
        body: Request body bytes
        signature: X-Hub-Signature-256 header value

    Returns:
        True if signature is valid, False otherwise
    """
    if not settings.strava_webhook_verify_token:
        logger.warning("[WEBHOOK] STRAVA_WEBHOOK_VERIFY_TOKEN not set, skipping signature verification")
        return True  # Allow in dev mode if token not set

    # Extract signature from header (format: sha256=...)
    if not signature.startswith("sha256="):
        logger.warning(f"[WEBHOOK] Invalid signature format: {signature[:20]}...")
        return False

    expected_signature = signature[7:]  # Remove "sha256=" prefix

    # Compute HMAC SHA256
    secret = settings.strava_webhook_verify_token.encode()
    computed_signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    # Constant-time comparison
    is_valid = hmac.compare_digest(computed_signature, expected_signature)

    if not is_valid:
        logger.warning("[WEBHOOK] Invalid webhook signature")

    return is_valid


@router.get("")
def webhook_verification(
    hub_mode: str | None = None,
    hub_challenge: str | None = None,
    hub_verify_token: str | None = None,
):
    """Handle Strava webhook subscription verification.

    Strava calls this endpoint during webhook subscription setup to verify
    the endpoint is valid and owned by the application.

    Args:
        hub_mode: Must be "subscribe"
        hub_challenge: Challenge string from Strava
        hub_verify_token: Verification token (must match STRAVA_WEBHOOK_VERIFY_TOKEN)

    Returns:
        JSON with hub.challenge if verification succeeds

    Raises:
        HTTPException: If verification fails
    """
    logger.info("[WEBHOOK] Webhook verification request received")

    # Verify mode
    if hub_mode != "subscribe":
        logger.warning(f"[WEBHOOK] Invalid hub.mode: {hub_mode}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid hub.mode. Must be 'subscribe'.",
        )

    # Verify token
    expected_token = getattr(settings, "strava_webhook_verify_token", None)
    if not expected_token:
        logger.warning("[WEBHOOK] STRAVA_WEBHOOK_VERIFY_TOKEN not configured, accepting verification")
        # In dev mode, accept if token not configured
        if hub_challenge:
            return {"hub.challenge": hub_challenge}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="hub.challenge is required",
        )

    if hub_verify_token != expected_token:
        token_preview = hub_verify_token[:10] if hub_verify_token else "None"
        logger.warning(f"[WEBHOOK] Invalid hub.verify_token: {token_preview}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid hub.verify_token",
        )

    if not hub_challenge:
        logger.warning("[WEBHOOK] Missing hub.challenge")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="hub.challenge is required",
        )

    logger.info("[WEBHOOK] Webhook verification successful")
    return {"hub.challenge": hub_challenge}


@router.post("")
async def webhook_event(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    """Handle Strava webhook events.

    Receives activity creation/update events from Strava and triggers
    background sync for the affected user.

    Args:
        request: FastAPI request object
        x_hub_signature_256: Webhook signature header

    Returns:
        Success response

    Raises:
        HTTPException: If signature verification fails
    """
    logger.info("[WEBHOOK] Webhook event received")

    # Read request body
    body = await request.body()

    # Verify signature
    if x_hub_signature_256:
        if not _verify_webhook_signature(body, x_hub_signature_256):
            logger.warning("[WEBHOOK] Webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook signature",
            )
    else:
        logger.warning("[WEBHOOK] Missing X-Hub-Signature-256 header")

    # Parse webhook event
    try:
        event_data = json.loads(body.decode())
    except Exception as e:
        logger.error(f"[WEBHOOK] Failed to parse webhook event: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON: {e}",
        ) from e

    # Extract event information
    object_type = event_data.get("object_type")
    aspect_type = event_data.get("aspect_type")
    owner_id = event_data.get("owner_id")
    object_id = event_data.get("object_id")

    logger.info(
        f"[WEBHOOK] Webhook event: object_type={object_type}, aspect_type={aspect_type}, owner_id={owner_id}, object_id={object_id}"
    )

    # Only process activity creation events
    if object_type != "activity" or aspect_type != "create":
        logger.debug(f"[WEBHOOK] Ignoring event: object_type={object_type}, aspect_type={aspect_type}")
        return {"status": "ignored", "reason": "Not an activity creation event"}

    # Find user_id from owner_id (athlete_id)
    # Note: owner_id is the Strava athlete_id (string)
    if not owner_id:
        logger.warning("[WEBHOOK] Missing owner_id in webhook event")
        return {"status": "error", "reason": "Missing owner_id"}

    # Map athlete_id to user_id via StravaAccount
    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(owner_id))).first()

        if not account:
            logger.warning(f"[WEBHOOK] No StravaAccount found for athlete_id={owner_id}")
            return {"status": "ignored", "reason": "No account found for athlete_id"}

        account_obj = account[0]
        user_id = account_obj.user_id

    # Trigger background sync for this user
    # Note: This runs synchronously in the webhook handler for simplicity.
    # In production, you might want to enqueue this to a job queue.
    logger.info(f"[WEBHOOK] Triggering sync for user_id={user_id} (athlete_id={owner_id})")
    try:
        result = sync_user_activities(user_id)
        if "error" in result:
            logger.warning(f"[WEBHOOK] Sync failed for user_id={user_id}: {result.get('error')}")
            return {"status": "sync_failed", "error": result.get("error")}
        logger.info(f"[WEBHOOK] Sync successful for user_id={user_id}: {result}")
    except Exception as e:
        logger.error(f"[WEBHOOK] Unexpected error during sync for user_id={user_id}: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    else:
        return {"status": "success", "user_id": user_id, "result": result}
