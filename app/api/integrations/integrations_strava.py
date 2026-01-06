"""Strava integration status endpoint.

Provides read-only status information about user's Strava connection.
Never exposes tokens or sensitive data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import StravaAccount
from app.db.session import get_session

router = APIRouter(prefix="/integrations/strava", tags=["integrations", "strava"])


@router.get("/status")
def strava_status(user_id: str = Depends(get_current_user_id)):
    """Get Strava connection status for current user.

    Returns connection status, athlete_id, and last_sync_at.
    Never exposes tokens or sensitive data.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Status response with connected, athlete_id, and last_sync_at
    """
    logger.info(f"[STRAVA_STATUS] Status check for user_id={user_id}")

    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account:
            logger.debug(f"[STRAVA_STATUS] No connection for user_id={user_id}")
            return {
                "connected": False,
                "athlete_id": None,
                "last_sync_at": None,
            }

        account_obj = account[0]
        logger.info(f"[STRAVA_STATUS] Connection found for user_id={user_id}, athlete_id={account_obj.athlete_id}")

        return {
            "connected": True,
            "athlete_id": account_obj.athlete_id,
            "last_sync_at": account_obj.last_sync_at,
        }
