"""Scheduler for background Strava activity sync.

Step 5: Runs periodic sync jobs for all users with Strava accounts.
"""

from __future__ import annotations

from loguru import logger

from app.services.ingestion.background_sync import sync_all_users


def sync_tick() -> None:
    """Run one sync cycle for all users.

    This function is called by APScheduler to sync activities for all users
    with Strava accounts. Runs every 6 hours by default.
    """
    logger.info("[SCHEDULER] Starting background sync tick")
    try:
        result = sync_all_users()
        logger.info(
            f"[SCHEDULER] Background sync complete: {result.get('successful', 0)}/{result.get('total_users', 0)} users synced successfully"
        )
    except Exception as e:
        logger.exception("[SCHEDULER] Background sync tick failed: {}", e)
