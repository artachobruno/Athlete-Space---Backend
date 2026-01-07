import time
from typing import TypedDict

from loguru import logger

from app.db.models import StravaAccount
from app.db.session import get_session
from app.ingestion.quota_manager import quota_manager
from app.ingestion.tasks import backfill_task, history_backfill_task, incremental_task
from app.models import StravaAuth

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


def _run_history_backfill_tasks() -> None:
    """Run history backfill tasks with dynamic quota allocation.

    Dynamically allocates available API quota across users who need backfill.
    As users complete, quota is redistributed to remaining users.
    Maximizes throughput by using as much available quota as possible.
    """
    with get_session() as session:
        accounts = session.query(StravaAccount).all()

        if not accounts:
            logger.debug("[SCHEDULER] No StravaAccount users found for history backfill")
            return

        # Filter to only users who need backfill
        users_needing_backfill = [account for account in accounts if not account.full_history_synced]

        if not users_needing_backfill:
            logger.debug("[SCHEDULER] All users have completed history backfill")
            return

        logger.info(f"[SCHEDULER] Found {len(users_needing_backfill)} user(s) needing history backfill (out of {len(accounts)} total)")

        # Process users with dynamic quota allocation
        # Continue until quota is exhausted or all users are processed
        processed_count = 0
        completed_count = 0

        while users_needing_backfill:
            # Check available quota
            max_requests = quota_manager.get_max_requests_available()

            if max_requests <= 0:
                logger.info(
                    f"[SCHEDULER] Quota exhausted. Processed {processed_count} users, "
                    f"{completed_count} completed, {len(users_needing_backfill)} remaining"
                )
                break

            # Calculate how many requests to allocate per user
            # Distribute available quota evenly across remaining users
            users_remaining = len(users_needing_backfill)
            requests_per_user = max(1, max_requests // users_remaining)

            # But limit to reasonable per-user allocation to avoid one user consuming all quota
            # Allow more requests per user if we have few users, but cap at 10 per user per cycle
            requests_per_user = min(requests_per_user, 10)

            logger.info(
                f"[SCHEDULER] Allocating quota: {max_requests} available, "
                f"{users_remaining} users remaining, ~{requests_per_user} requests per user"
            )

            # Process users, removing completed ones as we go
            users_to_remove = []

            for account in users_needing_backfill:
                user_id = account.user_id

                # Re-check if completed (may have been completed by another process)
                session.refresh(account)
                if account.full_history_synced:
                    logger.debug(f"[SCHEDULER] User {user_id} already completed, skipping")
                    users_to_remove.append(account)
                    completed_count += 1
                    continue

                # Check quota before each request
                if quota_manager.get_max_requests_available() <= 0:
                    logger.info("[SCHEDULER] Quota exhausted during processing, stopping")
                    break

                try:
                    logger.info(f"[SCHEDULER] Running history backfill for user_id={user_id}")
                    history_backfill_task(user_id)
                    processed_count += 1

                    # Re-check if completed after this run
                    session.refresh(account)
                    if account.full_history_synced:
                        logger.info(f"[SCHEDULER] History backfill completed for user_id={user_id}")
                        users_to_remove.append(account)
                        completed_count += 1
                    else:
                        logger.info(f"[SCHEDULER] History backfill progress for user_id={user_id} (not yet complete)")

                except Exception as e:
                    logger.error(
                        f"[SCHEDULER] History backfill failed for user_id={user_id}: {e}",
                        exc_info=True,
                    )
                    # Don't remove on error - user still needs backfill
                    processed_count += 1

            # Remove completed users from list
            for account in users_to_remove:
                users_needing_backfill.remove(account)

            # If we processed all users but none completed, we've hit a limit
            # Break to avoid infinite loop
            if not users_to_remove and processed_count > 0:
                logger.info(
                    f"[SCHEDULER] Processed {processed_count} users but none completed. "
                    f"May have hit rate limits or all users are at their cursor limits. "
                    f"Stopping cycle."
                )
                break

        logger.info(
            f"[SCHEDULER] History backfill cycle complete: "
            f"{processed_count} processed, {completed_count} completed, "
            f"{len(users_needing_backfill)} remaining"
        )


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

    # 3️⃣ History backfill for StravaAccount users (new system)
    _run_history_backfill_tasks()
