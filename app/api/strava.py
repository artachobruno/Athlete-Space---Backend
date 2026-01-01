from __future__ import annotations

import datetime as dt
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import func, select

from app.core.settings import settings
from app.db import update_last_ingested_at
from app.ingestion.save_activities import save_activity_record
from app.ingestion.tasks import backfill_task, incremental_task
from app.integrations.strava.client import StravaClient
from app.integrations.strava.oauth import exchange_code_for_token
from app.integrations.strava.schemas import map_strava_activity
from app.integrations.strava.token_service import TokenService
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
    try:
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if result:
                auth = result[0]
                # Check if activities exist - use func.count to avoid id column dependency
                result_count = session.execute(select(func.count(Activity.activity_id))).scalar()
                activity_count = result_count if result_count is not None else 0
                return {
                    "connected": True,
                    "athlete_id": auth.athlete_id,
                    "activity_count": activity_count,
                }
            return {"connected": False, "activity_count": 0}
    except Exception as e:
        logger.error(f"Error checking Strava status: {e}")
        return {"connected": False, "error": str(e), "activity_count": 0}


@router.get("/strava/sync-progress")
def strava_sync_progress():
    """Get sync progress information including activity count and sync status."""
    logger.debug("Sync progress check requested")
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
            result_count = session.execute(select(func.count(Activity.activity_id))).scalar()
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
def strava_sync():
    """Manually trigger async Strava sync."""
    try:
        logger.info("Manual Strava sync requested")
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if not result:
                logger.warning("Strava sync requested but no Strava connection found")
                return {"success": False, "error": "Strava not connected"}

            auth = result[0]
            logger.info(f"Found Strava auth for athlete_id={auth.athlete_id}")

        # Enqueue async ingestion (safe, scalable)
        logger.info(f"Enqueuing ingestion tasks for athlete_id={auth.athlete_id}")
        incremental_result = incremental_task.delay(auth.athlete_id)
        backfill_result = backfill_task.delay(auth.athlete_id)
        logger.info(f"Ingestion tasks enqueued: incremental_task_id={incremental_result.id}, backfill_task_id={backfill_result.id}")
    except Exception as e:
        logger.error(f"Error triggering Strava sync: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    else:
        return {
            "success": True,
            "status": "enqueued",
            "athlete_id": auth.athlete_id,
            "message": "Ingestion tasks enqueued. Ensure Celery workers are running to process them.",
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
    logger.info("Strava OAuth connect initiated")
    url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        "&scope=activity:read_all"
        "&approval_prompt=auto"
    )
    logger.debug(f"Redirecting to Strava OAuth: {url[:50]}...")
    return RedirectResponse(url)


def _perform_immediate_sync(access_token: str, athlete_id: int) -> int:
    """Perform immediate synchronous activity sync.

    Returns:
        Number of activities synced
    """
    logger.info("Starting immediate activity ingestion")
    activities_synced = 0

    try:
        client = StravaClient(access_token=access_token)
        # Fetch last 30 days of activities for initial sync
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        activities = client.fetch_recent_activities(after=since, per_page=100)

        if not activities:
            logger.info("No activities found for immediate sync")
            return 0

        logger.info(f"Fetched {len(activities)} activities for immediate sync")

        # Commit incrementally to make progress visible (each activity commits individually)
        batch_size = 10  # Log progress every 10 activities
        newest_ts = 0

        for activity in activities:
            try:
                # Use individual session per activity for immediate visibility
                with get_session() as session:
                    record = map_strava_activity(activity, athlete_id=athlete_id)
                    save_activity_record(session, record)
                    activities_synced += 1
                    newest_ts = max(newest_ts, int(activity.start_date.timestamp()))

                    # Log progress every 10 activities
                    if activities_synced % batch_size == 0:
                        logger.info(
                            f"Immediate sync progress: {activities_synced}/{len(activities)} "
                            f"activities saved ({int(activities_synced / len(activities) * 100)}%)"
                        )
            except Exception as e:
                logger.warning(f"Failed to save activity {activity.id}: {e}")

        # Update last_ingested_at after all activities are saved
        if newest_ts > 0:
            update_last_ingested_at(athlete_id, newest_ts)
        else:
            logger.warning("No activities to update last_ingested_at")

        logger.info(f"Immediate sync completed: {activities_synced} activities saved")
    except Exception as e:
        logger.error(f"Immediate sync failed (non-fatal): {e}", exc_info=True)
        return 0
    else:
        return activities_synced


@router.get("/strava/callback", response_class=HTMLResponse)
def strava_callback(code: str, request: Request):
    """Handle Strava OAuth callback and persist tokens.

    After OAuth exchange, we persist only:
    - athlete_id (from athlete.id in response)
    - refresh_token
    - expires_at

    Access tokens are never persisted - they are ephemeral.
    """
    logger.info("Strava OAuth callback received")

    # Determine frontend URL from settings or infer from request
    redirect_url = settings.frontend_url

    # If using default localhost, try to detect production URL from request
    if redirect_url == "http://localhost:8501":
        host = request.headers.get("host", "")

        # Check if we're on Render (any Render service)
        if "onrender.com" in host:
            # Default to pace-ai frontend when on Render
            # This works even if backend is on a different Render service
            redirect_url = "https://pace-ai.onrender.com"
        elif host and not host.startswith("localhost"):
            # For other production environments, use the request host with https
            redirect_url = f"https://{host}"

    logger.info(f"Redirecting to frontend: {redirect_url}")

    try:
        logger.debug("Exchanging authorization code for tokens")
        token_data = exchange_code_for_token(
            client_id=STRAVA_CLIENT_ID,
            client_secret=STRAVA_CLIENT_SECRET,
            code=code,
            redirect_uri=STRAVA_REDIRECT_URI,
        )

        # Extract data from OAuth response
        athlete_id = token_data["athlete"]["id"]
        refresh_token = token_data["refresh_token"]
        expires_at = token_data["expires_at"]
        access_token = token_data["access_token"]

        logger.info(f"OAuth successful for athlete_id={athlete_id}")

        # Persist tokens (only refresh_token and expires_at, not access_token)
        logger.debug("Saving tokens to database")
        with get_session() as session:
            token_service = TokenService(session)
            token_service.save_tokens(
                athlete_id=athlete_id,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
        logger.info("Tokens saved successfully")

        # Immediate synchronous ingestion using the access token we have
        _perform_immediate_sync(access_token, athlete_id)

        # Also enqueue async tasks for background sync and backfill
        logger.info("Enqueuing background ingestion tasks")
        incremental_task.delay(athlete_id)
        backfill_task.delay(athlete_id)
        logger.info("Background ingestion tasks enqueued")

    except Exception as e:
        logger.error(f"Error in Strava callback: {e}", exc_info=True)
        raise

    return f"""
    <html>
    <head>
        <title>Strava Connected</title>
        <meta http-equiv="refresh" content="3;url={redirect_url}">
    </head>
    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
        <h2 style="color: #4FC3F7;">✓ Strava Connected Successfully!</h2>
        <p><small>Redirecting to Virtus AI...</small></p>
        <p><a href="{redirect_url}">Click here if not redirected</a></p>
    </body>
    </html>
    """
