from __future__ import annotations

from loguru import logger

from app.db import (
    mark_backfill_done,
    store_activity,
    update_backfill_page,
)
from app.integrations.strava.service import get_strava_client

MAX_PAGES_PER_RUN = 3
PER_PAGE = 30


def backfill_user(user):
    """Backfill a user's full Strava activity history.

    - Runs incrementally
    - Safe to pause/resume
    - Respects global Strava quota
    """
    if user.backfill_done:
        logger.info(f"[BACKFILL] Skipping - backfill already done for athlete_id={user.athlete_id}")
        return

    logger.info(f"[BACKFILL] Starting backfill for athlete_id={user.athlete_id}, page={user.backfill_page or 1}")
    client = get_strava_client(user.athlete_id)
    page = user.backfill_page or 1
    total_saved = 0

    for run_num in range(MAX_PAGES_PER_RUN):
        logger.info(f"[BACKFILL] Fetching page {page} for athlete_id={user.athlete_id} (run {run_num + 1}/{MAX_PAGES_PER_RUN})")
        activities = client.fetch_backfill_page(
            page=page,
            per_page=PER_PAGE,
        )

        if not activities:
            logger.info(f"[BACKFILL] No more activities found at page {page}, marking backfill as done for athlete_id={user.athlete_id}")
            mark_backfill_done(user.athlete_id)
            logger.info(f"[BACKFILL] Backfill completed for athlete_id={user.athlete_id}, total saved in this run: {total_saved}")
            return

        logger.info(f"[BACKFILL] Fetched {len(activities)} activities from page {page} for athlete_id={user.athlete_id}")

        saved_count = 0
        for act in activities:
            try:
                store_activity(
                    user_id=user.athlete_id,
                    source="strava",
                    activity_id=str(act.id),
                    start_time=act.start_date,
                    raw=act.raw,
                )
                saved_count += 1
                total_saved += 1
            except Exception as e:
                logger.error(f"[BACKFILL] Failed to save activity {act.id} for athlete_id={user.athlete_id}: {e}")

        logger.info(f"[BACKFILL] Saved {saved_count}/{len(activities)} activities from page {page} for athlete_id={user.athlete_id}")

        page += 1
        update_backfill_page(user.athlete_id, page)

    logger.info(f"[BACKFILL] Completed {MAX_PAGES_PER_RUN} pages for athlete_id={user.athlete_id}, total saved: {total_saved}")
