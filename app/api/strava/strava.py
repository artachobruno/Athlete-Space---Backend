from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.db.models import Activity, StravaAccount, StravaAuth
from app.db.session import get_session
from app.ingestion.tasks import backfill_task, incremental_task
from app.metrics.daily_aggregation import aggregate_daily_training

STRAVA_CLIENT_ID = settings.strava_client_id
STRAVA_CLIENT_SECRET = settings.strava_client_secret
STRAVA_REDIRECT_URI = settings.strava_redirect_uri

router = APIRouter()


@router.get("/strava/status")
def strava_status():
    """Check if Strava is connected."""
    logger.info("[API] /strava/status endpoint called")
    try:
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if result:
                auth = result[0]
                # Check if activities exist - use func.count to avoid id column dependency
                result_count = session.execute(select(func.count(Activity.id))).scalar()
                activity_count = result_count if result_count is not None else 0
                logger.info(f"[API] Strava status: connected=True, athlete_id={auth.athlete_id}, activity_count={activity_count}")
                return {
                    "connected": True,
                    "athlete_id": auth.athlete_id,
                    "activity_count": activity_count,
                }
            logger.info("[API] Strava status: connected=False, no auth found")
            return {"connected": False, "activity_count": 0}
    except Exception as e:
        logger.error(f"[API] Error checking Strava status: {e}")
        return {"connected": False, "error": str(e), "activity_count": 0}


@router.get("/strava/sync-progress")
def strava_sync_progress(user_id: str = Depends(get_current_user_id)):
    """Get sync progress information for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with last_sync, sync_in_progress, and total_activities
    """
    logger.info(f"[API] /strava/sync-progress endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            # Get StravaAccount for user
            account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if not account_result:
                logger.debug(f"Sync progress: No Strava account found for user_id={user_id}")
                return {
                    "last_sync": None,
                    "sync_in_progress": False,
                    "total_activities": 0,
                }

            account = account_result[0]

            # Count activities for this user
            activity_count_result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
            total_activities = activity_count_result if activity_count_result is not None else 0

            # Determine if sync is in progress
            sync_in_progress = not account.full_history_synced

            # Format last_sync timestamp
            last_sync = None
            if account.last_sync_at:
                last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat()

            logger.info(
                f"Sync progress for user_id={user_id}: "
                f"total_activities={total_activities}, sync_in_progress={sync_in_progress}, "
                f"last_sync={last_sync}"
            )

            return {
                "last_sync": last_sync,
                "sync_in_progress": sync_in_progress,
                "total_activities": total_activities,
            }
    except Exception as e:
        logger.exception(f"Error getting sync progress for user_id={user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get sync progress: {e!s}") from e


@router.post("/strava/sync")
def strava_sync(background_tasks: BackgroundTasks):
    """Manually trigger async Strava sync."""
    try:
        logger.info("Manual Strava sync requested")
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if not result:
                logger.warning("Strava sync requested but no Strava connection found")
                return {"success": False, "error": "Strava not connected"}

            auth = result[0]
            # Extract athlete_id while session is still open
            athlete_id = auth.athlete_id
            logger.info(f"Found Strava auth for athlete_id={athlete_id}")

        # Schedule background ingestion tasks
        logger.info(f"Scheduling ingestion tasks for athlete_id={athlete_id}")
        background_tasks.add_task(incremental_task, athlete_id)
        background_tasks.add_task(backfill_task, athlete_id)
        logger.info(f"Ingestion tasks scheduled for athlete_id={athlete_id}")
    except Exception as e:
        logger.exception("Error triggering Strava sync")
        return {"success": False, "error": str(e)}
    else:
        return {
            "success": True,
            "status": "syncing",
            "athlete_id": athlete_id,
            "message": "Ingestion tasks scheduled to run in the background.",
        }


@router.post("/strava/aggregate")
def strava_aggregate():
    """Manually trigger daily aggregation to update daily_training_summary."""
    try:
        logger.info("[API] Manual aggregation requested")
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if not result:
                logger.warning("Aggregation requested but no Strava connection found")
                return {"success": False, "error": "Strava not connected"}

            auth = result[0]
            athlete_id = auth.athlete_id
            logger.info(f"[API] Triggering aggregation for athlete_id={athlete_id}")

            # Get user_id from StravaAccount (athlete_id is int, need to convert to str for lookup)
            account = session.query(StravaAccount).filter_by(athlete_id=str(athlete_id)).first()
            if not account:
                logger.warning(f"[API] No StravaAccount found for athlete_id={athlete_id}")
                return {"success": False, "error": "Strava account not found"}

            user_id = account.user_id
            logger.info(f"[API] Found user_id={user_id} for athlete_id={athlete_id}")

        # Run aggregation synchronously (it's fast)
        aggregate_daily_training(user_id)
    except Exception as e:
        logger.exception("[API] Error triggering aggregation")
        return {"success": False, "error": str(e)}
    else:
        return {
            "success": True,
            "athlete_id": athlete_id,
            "message": "Daily aggregation completed successfully",
        }


@router.get("/strava/connect")
def strava_connect():
    """DEPRECATED: Use /auth/strava instead.

    This endpoint is disabled. Use the authenticated endpoint at /auth/strava.
    """
    logger.error("[DEPRECATED] /strava/connect called - use /auth/strava instead")
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This endpoint is deprecated. Use /auth/strava for OAuth connection.",
    )


@router.get("/strava/callback")
def strava_callback():
    """DEPRECATED: Use /auth/strava/callback instead.

    This endpoint is disabled. Use the authenticated endpoint at /auth/strava/callback.
    """
    logger.error("[DEPRECATED] /strava/callback called - use /auth/strava/callback instead")
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This endpoint is deprecated. Use /auth/strava/callback for OAuth callback.",
    )
