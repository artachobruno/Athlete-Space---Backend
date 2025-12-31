from __future__ import annotations

import asyncio
import inspect
import time

import redis
from loguru import logger

from app.core.settings import settings

# Redis keys
KEY_15M_USED = "strava:quota:15m:used"
KEY_DAILY_USED = "strava:quota:daily:used"


class RedisStravaQuotaManager:
    """Redis-backed Strava quota manager shared across all workers."""

    LIMIT_15M = 100
    LIMIT_DAILY = 1000

    SAFE_15M = 90
    SAFE_DAILY = 950

    TTL_15M = 15 * 60
    TTL_DAILY = 24 * 60 * 60

    def __init__(self) -> None:
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    def _get_int(self, key: str) -> int:
        value = self.redis.get(key)

        if inspect.isawaitable(value):
            value = asyncio.get_event_loop().run_until_complete(value)

        return int(value) if value is not None else 0

    def _set_with_ttl(self, key: str, value: int, ttl_seconds: int) -> None:
        ttl: int = int(ttl_seconds)  # 🔑 makes Pyright happy
        pipe = self.redis.pipeline()
        pipe.set(key, value)
        pipe.expire(key, ttl)
        pipe.execute()

    def can_make_call(self) -> bool:
        return self._get_int(KEY_15M_USED) < self.SAFE_15M and self._get_int(KEY_DAILY_USED) < self.SAFE_DAILY

    def wait_for_slot(self) -> None:
        """Block until safely under Strava limits."""
        while not self.can_make_call():
            logger.warning("Strava quota exhausted — waiting")
            time.sleep(5)

    def update_from_headers(self, headers: dict[str, str]) -> None:
        usage = headers.get("X-RateLimit-Usage")
        if not usage:
            return

        used_15m, used_daily = map(int, usage.split(","))

        self._set_with_ttl(KEY_15M_USED, used_15m, self.TTL_15M)
        self._set_with_ttl(KEY_DAILY_USED, used_daily, self.TTL_DAILY)

        logger.debug(f"Updated Strava quota: 15m={used_15m}/{self.LIMIT_15M}, daily={used_daily}/{self.LIMIT_DAILY}")


quota_manager = RedisStravaQuotaManager()
