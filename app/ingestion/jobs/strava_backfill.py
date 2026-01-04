from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select

from app.db import (
    mark_backfill_done,
    store_activity,
    update_backfill_page,
)
from app.integrations.strava.service import get_strava_client
from app.state.db import get_session
from app.state.models import Activity, StravaAccount

MAX_PAGES_PER_RUN = 3
PER_PAGE = 30


def _check_activities_exist(athlete_id: int) -> bool:
    """Check if any activities exist for this athlete.

    Args:
        athlete_id: Strava athlete ID

    Returns:
        True if activities exist, False otherwise
    """
    with get_session() as session:
        # Map athlete_id to user_id
        account = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()
        if not account:
            return False
        user_id = account[0].user_id

        # Check if any activities exist
        count_result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
        return (count_result or 0) > 0


def backfill_user(user):
    """Backfill a user's full Strava activity history.

    - Runs incrementally
    - Safe to pause/resume
    - Respects global Strava quota
    """
    # Note: backfill_done check and reset is now handled in backfill_task
    # This function assumes backfill_done has already been checked/reset

    logger.info(f"[BACKFILL] Starting backfill for athlete_id={user.athlete_id}, page={user.backfill_page or 1}")
    client = get_strava_client(user.athlete_id)
    page = user.backfill_page or 1
    total_saved = 0
    total_errors = 0

    for run_num in range(MAX_PAGES_PER_RUN):
        logger.info(f"[BACKFILL] Fetching page {page} for athlete_id={user.athlete_id} (run {run_num + 1}/{MAX_PAGES_PER_RUN})")
        activities = client.fetch_backfill_page(
            page=page,
            per_page=PER_PAGE,
        )

        if not activities:
            # Only mark as done if we've saved at least some activities, or if this is page 1 (user has no activities)
            if total_saved > 0 or page == 1:
                logger.info(
                    f"[BACKFILL] No more activities found at page {page}, marking backfill as done for athlete_id={user.athlete_id} "
                    f"(total_saved={total_saved})"
                )
                mark_backfill_done(user.athlete_id)
            else:
                logger.warning(
                    f"[BACKFILL] No activities found at page {page} but no activities were saved. "
                    f"Not marking as done. Check for errors above."
                )
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
            except ValueError as e:
                # ValueError means StravaAccount lookup failed - this is a critical error
                logger.error(
                    f"[BACKFILL] CRITICAL: Cannot save activity {act.id} for athlete_id={user.athlete_id}: {e}. "
                    f"This usually means StravaAccount is missing. Stopping backfill."
                )
                total_errors += 1
                # Don't continue if we can't map athlete_id to user_id
                if total_errors >= 3:
                    logger.error(
                        f"[BACKFILL] Too many mapping errors ({total_errors}). Stopping backfill for athlete_id={user.athlete_id}"
                    )
                    return
            except Exception as e:
                logger.error(f"[BACKFILL] Failed to save activity {act.id} for athlete_id={user.athlete_id}: {e}", exc_info=True)
                total_errors += 1

        logger.info(f"[BACKFILL] Saved {saved_count}/{len(activities)} activities from page {page} for athlete_id={user.athlete_id}")

        page += 1
        update_backfill_page(user.athlete_id, page)

    logger.info(f"[BACKFILL] Completed {MAX_PAGES_PER_RUN} pages for athlete_id={user.athlete_id}, total saved: {total_saved}, errors: {total_errors}")
