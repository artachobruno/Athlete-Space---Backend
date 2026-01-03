from __future__ import annotations

import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from loguru import logger
from sqlalchemy import func, select

from app.core.settings import settings
from app.ingestion.tasks import backfill_task, incremental_task
from app.metrics.daily_aggregation import aggregate_daily_training
from app.state.db import get_session
from app.state.models import Activity, StravaAuth

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
def strava_sync_progress():
    """Get sync progress information including activity count and sync status."""
    logger.info("[API] /strava/sync-progress endpoint called")
    try:
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if not result:
                logger.debug("Sync progress: No Strava auth found")
                return {
                    "connected": False,
                    "activity_count": 0,
                    "sync_in_progress": False,
                    "progress_percentage": 0,
                }

            auth = result[0]
            # Use func.count to avoid id column dependency
            result_count = session.execute(select(func.count(Activity.id))).scalar()
            activity_count = result_count if result_count is not None else 0
            backfill_done = getattr(auth, "backfill_done", False)
            last_sync_at = auth.last_successful_sync_at if hasattr(auth, "last_successful_sync_at") else None

            logger.debug(
                f"Sync progress for athlete_id={auth.athlete_id}: "
                f"activity_count={activity_count}, backfill_done={backfill_done}, "
                f"last_sync_at={last_sync_at}"
            )

            # Determine if sync is in progress
            # Check if backfill is not done or if last sync was recent
            sync_in_progress = False
            sync_reason = None
            if hasattr(auth, "backfill_done") and not auth.backfill_done:
                sync_in_progress = True
                sync_reason = "backfill_not_done"
            elif last_sync_at:
                time_since_sync = time.time() - last_sync_at
                # If last sync was less than 5 minutes ago, consider it in progress
                if time_since_sync < 300:
                    sync_in_progress = True
                    sync_reason = f"recent_sync_{int(time_since_sync)}s_ago"

            # Estimate progress based on activity count
            # Rough estimate: 30 activities per day average, 14 days minimum = ~420 activities
            # For full history, could be 1000+ activities
            min_activities_needed = 14 * 2  # ~2 activities per day average
            estimated_total = max(min_activities_needed, activity_count + 100)  # Conservative estimate
            progress_percentage = min(100, int((activity_count / estimated_total) * 100)) if estimated_total > 0 else 0

            logger.info(
                f"Sync progress: athlete_id={auth.athlete_id}, "
                f"activities={activity_count}, progress={progress_percentage}%, "
                f"sync_in_progress={sync_in_progress}, reason={sync_reason}"
            )

            return {
                "connected": True,
                "athlete_id": auth.athlete_id,
                "activity_count": activity_count,
                "sync_in_progress": sync_in_progress,
                "progress_percentage": progress_percentage,
                "backfill_done": backfill_done,
                "last_sync_at": last_sync_at,
            }
    except Exception as e:
        logger.error(f"Error getting sync progress: {e}", exc_info=True)
        return {
            "connected": False,
            "activity_count": 0,
            "sync_in_progress": False,
            "progress_percentage": 0,
            "error": str(e),
        }


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
        logger.error(f"Error triggering Strava sync: {e}", exc_info=True)
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

        # Run aggregation synchronously (it's fast)
        aggregate_daily_training(athlete_id)
    except Exception as e:
        logger.error(f"[API] Error triggering aggregation: {e}", exc_info=True)
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
