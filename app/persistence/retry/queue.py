"""Redis-backed retry queue for persistence operations."""

import json
import time
from typing import Optional

import redis
from loguru import logger

from app.config.settings import settings
from app.persistence.retry.types import PlannedSessionRetryJob

QUEUE_KEY = "planned_sessions_retry"


def _get_redis_client() -> redis.Redis | None:
    """Get Redis client instance.

    Returns:
        Redis client if available, None otherwise (best-effort)
    """
    try:
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.bind(error=str(e)).warning("Failed to connect to Redis for retry queue")
        return None


def enqueue_retry(job: PlannedSessionRetryJob) -> None:
    """Enqueue a retry job for planned session persistence.

    This function NEVER raises exceptions. If Redis is unavailable,
    the job is silently dropped (best-effort retry).

    Args:
        job: Retry job to enqueue
    """
    redis_client = _get_redis_client()
    if not redis_client:
        logger.bind(plan_id=job.plan_id).warning("Redis unavailable, skipping retry enqueue")
        return

    try:
        job_dict = {
            "plan_id": job.plan_id,
            "user_id": job.user_id,
            "athlete_id": job.athlete_id,
            "sessions": job.sessions,
            "plan_type": job.plan_type,
            "created_at": job.created_at,
            "attempts": job.attempts,
        }
        redis_client.rpush(QUEUE_KEY, json.dumps(job_dict))
        logger.bind(plan_id=job.plan_id, attempts=job.attempts).debug("Enqueued persistence retry job")
    except Exception as e:
        logger.bind(plan_id=job.plan_id, error=str(e)).warning("Failed to enqueue persistence retry", exc_info=True)


def dequeue_retry() -> PlannedSessionRetryJob | None:
    """Dequeue a retry job from the queue.

    Returns:
        Retry job if available, None if queue is empty or Redis unavailable
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return None

    try:
        raw = redis_client.lpop(QUEUE_KEY)
        if not raw:
            return None

        if not isinstance(raw, str):
            logger.bind(raw_type=type(raw).__name__).warning("Unexpected type from Redis lpop")
            return None

        data = json.loads(raw)
        return PlannedSessionRetryJob(
            plan_id=data["plan_id"],
            user_id=data["user_id"],
            athlete_id=data["athlete_id"],
            sessions=data["sessions"],
            plan_type=data["plan_type"],
            created_at=data["created_at"],
            attempts=data["attempts"],
        )
    except Exception as e:
        logger.bind(error=str(e)).warning("Failed to dequeue persistence retry job", exc_info=True)
        return None
