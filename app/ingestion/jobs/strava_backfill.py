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


def _process_activity(act, user, page: int) -> tuple[bool, bool]:
    """Process a single activity for backfill.

    Args:
        act: Strava activity object
        user: StravaAuth user object
        page: Current backfill page number

    Returns:
        Tuple of (success: bool, is_critical_error: bool)
    """
    logger.debug(
        f"[BACKFILL] Processing activity {act.id} for athlete_id={user.athlete_id}, page={page}"
    )
    
    try:
        # Validate act.raw exists
        logger.debug(
            f"[BACKFILL] Validating activity {act.id}: has_raw={hasattr(act, 'raw')}, "
            f"raw_is_none={getattr(act, 'raw', None) is None}"
        )
        
        if not hasattr(act, 'raw') or act.raw is None:
            logger.error(
                f"[BACKFILL] Activity {act.id} missing raw data for athlete_id={user.athlete_id}"
            )
            return False, False
        
        raw_type = type(act.raw)
        raw_is_dict = isinstance(act.raw, dict)
        raw_keys = list(act.raw.keys()) if raw_is_dict else []
        raw_has_id = 'id' in act.raw if raw_is_dict else False
        
        logger.debug(
            f"[BACKFILL] Activity {act.id} raw data: type={raw_type}, is_dict={raw_is_dict}, "
            f"has_id={raw_has_id}, keys_count={len(raw_keys)}, "
            f"sample_keys={raw_keys[:10] if raw_keys else []}"
        )
        
        if raw_has_id:
            logger.debug(f"[BACKFILL] Activity {act.id} raw['id']: {act.raw.get('id')}, type: {type(act.raw.get('id'))}")
        
        logger.debug(
            f"[BACKFILL] Calling store_activity for activity {act.id}: "
            f"athlete_id={user.athlete_id}, start_date={act.start_date}"
        )
        
        store_activity(
            user_id=user.athlete_id,
            source="strava",
            activity_id=str(act.id),
            start_time=act.start_date,
            raw=act.raw,
        )
        
        logger.debug(f"[BACKFILL] store_activity completed successfully for activity {act.id}")
    except ValueError as e:
        logger.error(
            f"[BACKFILL] CRITICAL: Cannot save activity {act.id} for athlete_id={user.athlete_id}: {e}. "
            f"This usually means StravaAccount is missing. Check that StravaAccount exists with athlete_id={user.athlete_id}."
        )
        return False, True
    except KeyError as e:
        logger.error(
            f"[BACKFILL] KeyError saving activity {act.id} for athlete_id={user.athlete_id}: {e}. "
            f"Raw data type: {type(act.raw)}, has 'id': {'id' in act.raw if isinstance(act.raw, dict) else 'N/A'}",
            exc_info=True,
        )
        return False, False
    except Exception as e:
        activity_id = getattr(act, 'id', 'unknown')
        error_msg = str(e)
        logger.error(
            "[BACKFILL] Failed to save activity %s for athlete_id=%s: %s. Error type: %s",
            activity_id,
            user.athlete_id,
            error_msg,
            type(e).__name__,
            exc_info=True,
        )
        return False, False
    else:
        logger.debug(f"[BACKFILL] Successfully saved activity {act.id} from page {page}")
        return True, False


def _handle_no_activities(page: int, total_saved: int, total_errors: int, athlete_id: int) -> None:
    """Handle case when no activities are returned from API.

    Args:
        page: Current page number
        total_saved: Total activities saved in this run
        total_errors: Total errors encountered
        athlete_id: Strava athlete ID
    """
    if total_saved > 0 or page == 1:
        logger.info(
            f"[BACKFILL] No more activities found at page {page}, marking backfill as done for athlete_id={athlete_id} "
            f"(total_saved={total_saved})"
        )
        mark_backfill_done(athlete_id)
    else:
        logger.warning(
            f"[BACKFILL] No activities found at page {page} but no activities were saved in this run. "
            f"Total saved: {total_saved}, errors: {total_errors}. "
            f"Not marking as done - will retry from page 1 next time."
        )
        update_backfill_page(athlete_id, 1)


def check_activities_exist(athlete_id: int) -> bool:
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

    # Check if we should reset to page 1
    page = user.backfill_page or 1
    if page > 1 and not check_activities_exist(user.athlete_id):
        logger.warning(
            f"[BACKFILL] Starting at page {page} but no activities exist for athlete_id={user.athlete_id}. "
            f"Resetting to page 1."
        )
        page = 1
        update_backfill_page(user.athlete_id, 1)

    logger.info(f"[BACKFILL] Starting backfill for athlete_id={user.athlete_id}, page={page}")
    client = get_strava_client(user.athlete_id)
    total_saved = 0
    total_errors = 0

    for run_num in range(MAX_PAGES_PER_RUN):
        logger.info(f"[BACKFILL] Fetching page {page} for athlete_id={user.athlete_id} (run {run_num + 1}/{MAX_PAGES_PER_RUN})")
        activities = client.fetch_backfill_page(
            page=page,
            per_page=PER_PAGE,
        )

        if not activities:
            _handle_no_activities(page, total_saved, total_errors, user.athlete_id)
            logger.info(f"[BACKFILL] Backfill completed for athlete_id={user.athlete_id}, total saved in this run: {total_saved}")
            return

        logger.info(f"[BACKFILL] Fetched {len(activities)} activities from page {page} for athlete_id={user.athlete_id}")

        if activities:
            logger.debug(f"[BACKFILL] Sample activity IDs from page {page}: {[str(act.id) for act in activities[:3]]}")

        saved_count = 0
        for act in activities:
            success, is_critical = _process_activity(act, user, page)
            if success:
                saved_count += 1
                total_saved += 1
            else:
                total_errors += 1
                if is_critical and total_errors >= 3:
                    logger.error(
                        f"[BACKFILL] Too many mapping errors ({total_errors}). Stopping backfill for athlete_id={user.athlete_id}. "
                        f"Please check that StravaAccount exists and athlete_id matches."
                    )
                    return

        if activities and saved_count == 0:
            logger.error(
                f"[BACKFILL] CRITICAL: Fetched {len(activities)} activities from page {page} but saved 0. "
                f"Total errors: {total_errors}. This indicates a systematic issue with activity storage."
            )
        else:
            logger.info(f"[BACKFILL] Saved {saved_count}/{len(activities)} activities from page {page} for athlete_id={user.athlete_id}")

        page += 1
        update_backfill_page(user.athlete_id, page)

    logger.info(
        f"[BACKFILL] Completed {MAX_PAGES_PER_RUN} pages for athlete_id={user.athlete_id}, "
        f"total saved: {total_saved}, errors: {total_errors}"
    )
