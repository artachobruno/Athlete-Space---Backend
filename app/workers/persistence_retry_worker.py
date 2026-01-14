"""Background worker for retrying failed persistence operations."""

import time

from loguru import logger

from app.coach.tools.session_planner import save_sessions_to_database
from app.persistence.retry.queue import dequeue_retry, enqueue_retry
from app.persistence.retry.types import PlannedSessionRetryJob

MAX_ATTEMPTS = 5
POLL_INTERVAL_SECONDS = 2


def run_retry_worker() -> None:
    """Run the persistence retry worker loop.

    This worker continuously polls the retry queue and attempts to save
    planned sessions directly to the database, bypassing MCP.
    """
    logger.info("Starting persistence retry worker")

    while True:
        try:
            job = dequeue_retry()
            if not job:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if job.attempts >= MAX_ATTEMPTS:
                logger.warning(
                    "Retry job exceeded max attempts, dropping",
                    plan_id=job.plan_id,
                    attempts=job.attempts,
                    max_attempts=MAX_ATTEMPTS,
                )
                continue

            logger.info(
                "Processing retry job",
                plan_id=job.plan_id,
                attempts=job.attempts,
                session_count=len(job.sessions),
            )

            try:
                saved_count = save_sessions_to_database(
                    user_id=job.user_id,
                    athlete_id=job.athlete_id,
                    sessions=job.sessions,
                    plan_type=job.plan_type,
                    plan_id=job.plan_id,
                )

                if saved_count > 0:
                    logger.info(
                        "Retry job succeeded",
                        plan_id=job.plan_id,
                        saved_count=saved_count,
                        attempts=job.attempts,
                    )
                else:
                    # All sessions were duplicates or invalid - consider this success
                    logger.info(
                        "Retry job completed (no new sessions saved, likely duplicates)",
                        plan_id=job.plan_id,
                        attempts=job.attempts,
                    )
            except Exception as e:
                # Save failed - increment attempts and re-enqueue
                logger.warning(
                    "Retry job failed, re-enqueueing",
                    plan_id=job.plan_id,
                    attempts=job.attempts,
                    error=str(e),
                    error_type=type(e).__name__,
                )

                # Create updated job with incremented attempts
                updated_job = PlannedSessionRetryJob(
                    plan_id=job.plan_id,
                    user_id=job.user_id,
                    athlete_id=job.athlete_id,
                    sessions=job.sessions,
                    plan_type=job.plan_type,
                    created_at=job.created_at,
                    attempts=job.attempts + 1,
                )
                enqueue_retry(updated_job)

        except Exception as e:
            logger.error("Unexpected error in retry worker loop", extra={"error": str(e)}, exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_retry_worker()
