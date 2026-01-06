from __future__ import annotations

import uuid
from contextlib import contextmanager

import redis
from loguru import logger

from app.config.settings import settings

LOCK_TTL_SECONDS = 10 * 60  # 10 minutes


class RedisLockManager:
    def __init__(self) -> None:
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    @contextmanager
    def acquire(self, key: str):
        """Acquire a Redis lock using SET NX.

        If lock cannot be acquired, yields False.
        If acquired, yields True and guarantees release.
        """
        token = str(uuid.uuid4())

        acquired = self.redis.set(
            key,
            token,
            nx=True,
            ex=LOCK_TTL_SECONDS,
        )

        if not acquired:
            logger.debug(f"Lock busy, skipping: {key}")
            yield False
            return

        try:
            logger.debug(f"Lock acquired: {key}")
            yield True
        finally:
            # Safe release: only delete if token matches
            current = self.redis.get(key)
            if current == token:
                self.redis.delete(key)
                logger.debug(f"Lock released: {key}")


lock_manager = RedisLockManager()
