import time
from typing import TypedDict

from loguru import logger

from app.ingestion.tasks import backfill_task, incremental_task
from app.models import StravaAuth
from app.state.db import get_session

STUCK_BACKFILL_SECONDS = 3 * 60 * 60  # 3 hours


class UserData(TypedDict):
    athlete_id: int
    backfill_done: bool
    backfill_updated_at: int | None


def _run_incremental_tasks(user_data: list[UserData]) -> None:
    """Run incremental tasks for all users."""
    for user_info in user_data:
        athlete_id = user_info["athlete_id"]
        try:
            logger.info(f"[SCHEDULER] Running incremental task for athlete_id={athlete_id}")
            incremental_task(athlete_id)
            logger.info(f"[SCHEDULER] Incremental task completed for athlete_id={athlete_id}")
        except Exception as e:
            logger.error(
                f"[SCHEDULER] Incremental task failed for athlete_id={athlete_id}: {e}",
                exc_info=True,
            )


def _run_backfill_tasks(user_data: list[UserData], now: int) -> None:
    """Run backfill tasks for users who need them."""
    for user_info in user_data:
        athlete_id = user_info["athlete_id"]
        backfill_done = user_info["backfill_done"]
        backfill_updated_at = user_info["backfill_updated_at"]

        if not backfill_done:
            # Auto-heal stuck backfills
            if backfill_updated_at and now - backfill_updated_at > STUCK_BACKFILL_SECONDS:
                logger.warning(
                    f"[SCHEDULER] Auto-retrying stuck backfill for user={athlete_id} "
                    f"(last update: {(now - backfill_updated_at) // 60} min ago)"
                )
            try:
                logger.info(f"[SCHEDULER] Running backfill task for athlete_id={athlete_id}")
                backfill_task(athlete_id)
                logger.info(f"[SCHEDULER] Backfill task completed for athlete_id={athlete_id}")
            except Exception as e:
                logger.error(
                    f"[SCHEDULER] Backfill task failed for athlete_id={athlete_id}: {e}",
                    exc_info=True,
                )
        else:
            logger.debug(f"[SCHEDULER] Skipping backfill for athlete_id={athlete_id} (already done)")


def ingestion_tick() -> None:
    """Run one ingestion cycle.

    Rules:
    - Incremental tasks always run
    - Backfill tasks only run if needed
    - Stuck backfills are auto-retried
    - Tasks run synchronously in the scheduler thread
    """
    logger.info("[SCHEDULER] Running Strava ingestion tasks")

    now = int(time.time())

    with get_session() as session:
        users = session.query(StravaAuth).all()

        if not users:
            logger.warning("[SCHEDULER] No Strava users found to sync")
            return

        logger.info(f"[SCHEDULER] Found {len(users)} user(s) to sync")

        # Extract user data while session is open
        user_data_list: list[UserData] = []
        for user in users:
            athlete_id: int = user.athlete_id
            backfill_done_attr = getattr(user, "backfill_done", False)
            backfill_done: bool = backfill_done_attr if isinstance(backfill_done_attr, bool) else False
            backfill_updated_at: int | None = getattr(user, "backfill_updated_at", None)
            user_data_list.append({
                "athlete_id": athlete_id,
                "backfill_done": backfill_done,
                "backfill_updated_at": backfill_updated_at,
            })
        user_data = user_data_list

    # 1️⃣ Incrementals (cheap, priority)
    _run_incremental_tasks(user_data)

    # 2️⃣ Backfills (slow, background)
    _run_backfill_tasks(user_data, now)

    logger.info(f"[SCHEDULER] Strava ingestion tasks completed for {len(users)} user(s)")
