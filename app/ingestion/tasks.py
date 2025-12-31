import time

from loguru import logger

from app.celery_app import celery_app
from app.ingestion.jobs.strava_backfill import backfill_user
from app.ingestion.jobs.strava_incremental import incremental_sync_user
from app.ingestion.locks import lock_manager
from app.metrics.daily_aggregation import aggregate_daily_training
from app.models import StravaAuth
from app.state.db import get_session


def _record_error(session, user, exc: Exception) -> None:
    user.last_error = str(exc)
    user.last_error_at = int(time.time())
    session.add(user)


@celery_app.task(
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 5},
)
def incremental_task(athlete_id: int) -> None:
    """Incremental Strava sync task."""
    task_start = time.time()
    logger.info(f"[CELERY] Incremental task STARTED for athlete_id={athlete_id}")
    lock_key = f"lock:strava:user:{athlete_id}"

    with lock_manager.acquire(lock_key) as acquired:
        if not acquired:
            logger.warning(f"[CELERY] Could not acquire lock for incremental sync: athlete_id={athlete_id}")
            return

        with get_session() as session:
            user = session.query(StravaAuth).get(athlete_id)
            if not user:
                logger.warning(f"[CELERY] Incremental sync: user not found: athlete_id={athlete_id}")
                return

            try:
                logger.info(f"[CELERY] Executing incremental sync for athlete_id={athlete_id}")
                incremental_sync_user(user)
                user.last_successful_sync_at = int(time.time())
                user.last_error = None
                user.last_error_at = None
                session.add(user)
                session.commit()
                elapsed = time.time() - task_start
                logger.info(f"[CELERY] Incremental sync completed successfully for athlete_id={athlete_id} in {elapsed:.2f}s")

                # Trigger daily aggregation after successful ingestion
                # Do NOT block ingestion if aggregation fails
                try:
                    logger.debug(f"[CELERY] Triggering daily aggregation for athlete_id={athlete_id}")
                    aggregate_daily_training(user.athlete_id)
                    logger.debug(f"[CELERY] Daily aggregation completed for athlete_id={athlete_id}")
                except Exception as agg_error:
                    logger.error(
                        f"[CELERY] Aggregation failed for athlete_id={user.athlete_id}: {agg_error}",
                        exc_info=True,
                    )
                    # Continue - aggregation failure should not fail ingestion
            except Exception as e:
                elapsed = time.time() - task_start
                logger.error(f"[CELERY] Incremental sync failed for athlete_id={athlete_id} after {elapsed:.2f}s: {e}", exc_info=True)
                _record_error(session, user, e)
                raise


@celery_app.task(
    autoretry_for=(Exception,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 3},
)
def backfill_task(athlete_id: int) -> None:
    """Backfill Strava sync task."""
    task_start = time.time()
    logger.info(f"[CELERY] Backfill task STARTED for athlete_id={athlete_id}")
    lock_key = f"lock:strava:user:{athlete_id}"

    with lock_manager.acquire(lock_key) as acquired:
        if not acquired:
            logger.warning(f"[CELERY] Could not acquire lock for backfill sync: athlete_id={athlete_id}")
            return

        with get_session() as session:
            user = session.query(StravaAuth).get(athlete_id)
            if not user:
                logger.warning(f"[CELERY] Backfill sync: user not found: athlete_id={athlete_id}")
                return

            if user.backfill_done:
                logger.info(f"[CELERY] Backfill already completed for athlete_id={athlete_id}, skipping")
                return

            try:
                logger.info(f"[CELERY] Executing backfill sync for athlete_id={athlete_id}")
                backfill_user(user)
                user.last_error = None
                user.last_error_at = None
                session.add(user)
                session.commit()
                elapsed = time.time() - task_start
                logger.info(f"[CELERY] Backfill sync completed successfully for athlete_id={athlete_id} in {elapsed:.2f}s")
            except Exception as e:
                elapsed = time.time() - task_start
                logger.error(f"[CELERY] Backfill sync failed for athlete_id={athlete_id} after {elapsed:.2f}s: {e}", exc_info=True)
                _record_error(session, user, e)
                raise
