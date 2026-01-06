import time

from loguru import logger

from app.db.models import StravaAccount
from app.db.session import get_session
from app.ingestion.jobs.history_backfill import (
    HistoryBackfillError,
    RateLimitError,
    TokenRefreshError,
    backfill_user_history,
)
from app.ingestion.jobs.strava_backfill import backfill_user, check_activities_exist
from app.ingestion.jobs.strava_incremental import incremental_sync_user
from app.ingestion.locks import lock_manager
from app.metrics.daily_aggregation import aggregate_daily_training
from app.models import StravaAuth


def _record_error(session, user, exc: Exception) -> None:
    user.last_error = str(exc)
    user.last_error_at = int(time.time())
    session.add(user)


def incremental_task(athlete_id: int) -> None:
    """Incremental Strava sync task."""
    task_start = time.time()
    logger.info(f"[INGESTION] Incremental task STARTED for athlete_id={athlete_id}")
    lock_key = f"lock:strava:user:{athlete_id}"

    with lock_manager.acquire(lock_key) as acquired:
        if not acquired:
            logger.warning(f"[INGESTION] Could not acquire lock for incremental sync: athlete_id={athlete_id}")
            return

        with get_session() as session:
            user = session.query(StravaAuth).get(athlete_id)
            if not user:
                logger.warning(f"[INGESTION] Incremental sync: user not found: athlete_id={athlete_id}")
                return

            try:
                logger.info(f"[INGESTION] Executing incremental sync for athlete_id={athlete_id}")
                incremental_sync_user(user)
                user.last_successful_sync_at = int(time.time())
                user.last_error = None
                user.last_error_at = None
                session.add(user)
                session.commit()
                elapsed = time.time() - task_start
                logger.info(f"[INGESTION] Incremental sync completed successfully for athlete_id={athlete_id} in {elapsed:.2f}s")

                # Trigger daily aggregation after successful ingestion
                # Do NOT block ingestion if aggregation fails
                try:
                    # Get user_id from StravaAccount (athlete_id is int, need to convert to str for lookup)
                    account = session.query(StravaAccount).filter_by(athlete_id=str(athlete_id)).first()
                    if account:
                        logger.debug(f"[INGESTION] Triggering daily aggregation for athlete_id={athlete_id}, user_id={account.user_id}")
                        aggregate_daily_training(account.user_id)
                        logger.debug(f"[INGESTION] Daily aggregation completed for athlete_id={athlete_id}")
                    else:
                        logger.warning(f"[INGESTION] No StravaAccount found for athlete_id={athlete_id}, skipping aggregation")
                except Exception:
                    logger.exception(f"[INGESTION] Aggregation failed for athlete_id={athlete_id}")
                    # Continue - aggregation failure should not fail ingestion
            except Exception as e:
                elapsed = time.time() - task_start
                logger.exception(f"[INGESTION] Incremental sync failed for athlete_id={athlete_id} after {elapsed:.2f}s")
                _record_error(session, user, e)
                raise


def backfill_task(athlete_id: int) -> None:
    """Backfill Strava sync task."""
    task_start = time.time()
    logger.info(f"[INGESTION] Backfill task STARTED for athlete_id={athlete_id}")
    lock_key = f"lock:strava:user:{athlete_id}"

    with lock_manager.acquire(lock_key) as acquired:
        if not acquired:
            logger.warning(f"[INGESTION] Could not acquire lock for backfill sync: athlete_id={athlete_id}")
            return

        with get_session() as session:
            user = session.query(StravaAuth).get(athlete_id)
            if not user:
                logger.warning(f"[INGESTION] Backfill sync: user not found: athlete_id={athlete_id}")
                return

            if user.backfill_done:
                # If backfill is marked as done but no activities exist, reset it
                if not check_activities_exist(athlete_id):
                    logger.warning(
                        f"[INGESTION] Backfill marked as done but no activities found for athlete_id={athlete_id}. "
                        f"Resetting backfill_done to False and backfill_page to 1."
                    )
                    user.backfill_done = False
                    user.backfill_page = 1
                    session.add(user)
                    session.commit()
                else:
                    logger.info(f"[INGESTION] Backfill already completed for athlete_id={athlete_id}, skipping")
                    return

            # If no activities exist but we're at a high page number, reset to page 1
            if not check_activities_exist(athlete_id) and user.backfill_page and user.backfill_page > 1:
                logger.warning(
                    f"[INGESTION] No activities found but backfill_page={user.backfill_page} for athlete_id={athlete_id}. "
                    f"Resetting backfill_page to 1."
                )
                user.backfill_page = 1
                session.add(user)
                session.commit()

            try:
                logger.info(f"[INGESTION] Executing backfill sync for athlete_id={athlete_id}")
                backfill_user(user)
                user.last_error = None
                user.last_error_at = None
                session.add(user)
                session.commit()
                elapsed = time.time() - task_start
                logger.info(f"[INGESTION] Backfill sync completed successfully for athlete_id={athlete_id} in {elapsed:.2f}s")
            except Exception as e:
                elapsed = time.time() - task_start
                logger.exception(f"[INGESTION] Backfill sync failed for athlete_id={athlete_id} after {elapsed:.2f}s")
                _record_error(session, user, e)
                raise


def history_backfill_task(user_id: str) -> None:
    """History backfill task with backward-moving cursor.

    Uses StravaAccount model and user_id (string).
    """
    task_start = time.time()
    logger.info(f"[INGESTION] History backfill task STARTED for user_id={user_id}")
    lock_key = f"lock:strava:history:{user_id}"

    with lock_manager.acquire(lock_key) as acquired:
        if not acquired:
            logger.warning(f"[INGESTION] Could not acquire lock for history backfill: user_id={user_id}")
            return

        with get_session() as session:
            account = session.query(StravaAccount).filter_by(user_id=user_id).first()
            if not account:
                logger.warning(f"[INGESTION] History backfill: account not found: user_id={user_id}")
                return

            if account.full_history_synced:
                logger.info(f"[INGESTION] History backfill already completed for user_id={user_id}, skipping")
                return

            try:
                logger.info(f"[INGESTION] Executing history backfill for user_id={user_id}")
                backfill_user_history(user_id)
                elapsed = time.time() - task_start
                logger.info(f"[INGESTION] History backfill completed successfully for user_id={user_id} in {elapsed:.2f}s")

                # Trigger daily aggregation to update CTL, ATL, TSB metrics
                try:
                    logger.debug(f"[INGESTION] Triggering daily aggregation for user_id={user_id} after history backfill")
                    aggregate_daily_training(user_id)
                    logger.info(f"[INGESTION] Daily aggregation completed for user_id={user_id} after history backfill")
                except Exception as e:
                    logger.error(f"[INGESTION] Daily aggregation failed for user_id={user_id}: {e}", exc_info=True)
                    # Don't fail history backfill if aggregation fails
            except RateLimitError as e:
                elapsed = time.time() - task_start
                logger.warning(f"[INGESTION] History backfill rate limited for user_id={user_id} after {elapsed:.2f}s: {e}")
                raise
            except TokenRefreshError as e:
                elapsed = time.time() - task_start
                logger.error(f"[INGESTION] History backfill token error for user_id={user_id} after {elapsed:.2f}s: {e}")
                raise
            except HistoryBackfillError:
                elapsed = time.time() - task_start
                logger.exception(f"[INGESTION] History backfill failed for user_id={user_id} after {elapsed:.2f}s")
                raise
            except Exception:
                elapsed = time.time() - task_start
                logger.exception(f"[INGESTION] History backfill unexpected error for user_id={user_id} after {elapsed:.2f}s")
                raise
