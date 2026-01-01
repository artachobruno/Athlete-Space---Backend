import time

from celery.app.task import Task
from loguru import logger
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ingestion.tasks import backfill_task as _backfill_task
from app.ingestion.tasks import incremental_task as _incremental_task
from app.models import StravaAuth
from app.state.db import get_session

# Properly typed references to Celery tasks
incremental_task: Task = _incremental_task
backfill_task: Task = _backfill_task

STUCK_BACKFILL_SECONDS = 3 * 60 * 60  # 3 hours


def _enqueue_incremental_tasks(user_data: list[dict[str, int | bool | None]]) -> None:
    """Enqueue incremental tasks for all users."""
    for user_info in user_data:
        athlete_id = user_info["athlete_id"]
        try:
            logger.info(f"[SCHEDULER] Enqueuing incremental task for athlete_id={athlete_id}")
            result = incremental_task.delay(athlete_id)
            logger.info(f"[SCHEDULER] Incremental task enqueued: task_id={result.id} for athlete_id={athlete_id}")
        except RedisConnectionError as e:
            logger.error(
                f"[SCHEDULER] Redis connection failed while enqueuing incremental task for athlete_id={athlete_id}: {e}. "
                "Ensure Redis is running and REDIS_URL is configured correctly."
            )
        except RuntimeError as e:
            if "Retry limit exceeded" in str(e):
                logger.error(
                    f"[SCHEDULER] Celery backend connection failed for athlete_id={athlete_id}: {e}. "
                    "Celery application may need to be restarted."
                )
            else:
                raise


def _enqueue_backfill_tasks(user_data: list[dict[str, int | bool | None]], now: int) -> None:
    """Enqueue backfill tasks for users who need them."""
    for user_info in user_data:
        athlete_id = user_info["athlete_id"]
        backfill_done = user_info["backfill_done"]
        backfill_updated_at = user_info["backfill_updated_at"]

        if not backfill_done:
            # Auto-heal stuck backfills
            if backfill_updated_at and now - backfill_updated_at > STUCK_BACKFILL_SECONDS:
                logger.warning(
                    f"[SCHEDULER] Auto-requeuing stuck backfill for user={athlete_id} "
                    f"(last update: {(now - backfill_updated_at) // 60} min ago)"
                )
            try:
                logger.info(f"[SCHEDULER] Enqueuing backfill task for athlete_id={athlete_id}")
                result = backfill_task.delay(athlete_id)
                logger.info(f"[SCHEDULER] Backfill task enqueued: task_id={result.id} for athlete_id={athlete_id}")
            except RedisConnectionError as e:
                logger.error(
                    f"[SCHEDULER] Redis connection failed while enqueuing backfill task for athlete_id={athlete_id}: {e}. "
                    "Ensure Redis is running and REDIS_URL is configured correctly."
                )
            except RuntimeError as e:
                if "Retry limit exceeded" in str(e):
                    logger.error(
                        f"[SCHEDULER] Celery backend connection failed for athlete_id={athlete_id}: {e}. "
                        "Celery application may need to be restarted."
                    )
                else:
                    raise
        else:
            logger.debug(f"[SCHEDULER] Skipping backfill for athlete_id={athlete_id} (already done)")


def ingestion_tick() -> None:
    """Enqueue one ingestion cycle.

    Rules:
    - Incremental tasks always enqueued
    - Backfill tasks only enqueued if needed
    - Stuck backfills are auto-requeued
    - Celery workers execute the work
    """
    logger.info("[SCHEDULER] Enqueuing Strava ingestion tasks")

    now = int(time.time())

    with get_session() as session:
        users = session.query(StravaAuth).all()

        if not users:
            logger.warning("[SCHEDULER] No Strava users found to sync")
            return

        logger.info(f"[SCHEDULER] Found {len(users)} user(s) to sync")

        # Extract user data while session is open
        user_data = [
            {
                "athlete_id": user.athlete_id,
                "backfill_done": getattr(user, "backfill_done", False),
                "backfill_updated_at": getattr(user, "backfill_updated_at", None),
            }
            for user in users
        ]

    # 1️⃣ Incrementals (cheap, priority)
    _enqueue_incremental_tasks(user_data)

    # 2️⃣ Backfills (slow, background)
    _enqueue_backfill_tasks(user_data, now)

    logger.info(f"[SCHEDULER] Strava ingestion tasks enqueued for {len(users)} user(s)")
