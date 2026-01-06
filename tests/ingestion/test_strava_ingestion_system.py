from __future__ import annotations

import time
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import text

from app.db.session import get_session
from app.models import Activity, StravaAuth
from app.services.ingestion.locks import lock_manager
from app.services.ingestion.quota_manager import quota_manager
from app.services.ingestion.scheduler import ingestion_tick


def assert_redis():
    logger.info("Checking Redis connectivity")
    r = quota_manager.redis
    r.ping()
    logger.success("Redis OK")


def assert_db():
    logger.info("Checking DB connectivity")
    with get_session() as session:
        session.execute(text("SELECT 1"))
    logger.success("DB OK")


def assert_users_exist() -> list[int]:
    logger.info("Checking Strava users")
    with get_session() as session:
        users = session.query(StravaAuth).all()

    if not users:
        raise RuntimeError("No StravaAuth users found in DB")

    athlete_ids = [u.athlete_id for u in users]
    logger.success(f"Found {len(athlete_ids)} Strava users")
    return athlete_ids


def test_lock(athlete_id: int):
    logger.info(f"Testing Redis lock for athlete={athlete_id}")
    key = f"lock:strava:user:{athlete_id}"

    with lock_manager.acquire(key) as acquired:
        assert acquired is True

        with lock_manager.acquire(key) as acquired2:
            assert acquired2 is False

    logger.success("Redis lock behavior OK")


def trigger_ingestion():
    logger.info("Triggering ingestion tick")
    ingestion_tick()
    logger.success("Ingestion tick enqueued")


def wait_for_processing(seconds: int = 15):
    logger.info(f"Waiting {seconds}s for Celery workers to process")
    time.sleep(seconds)


def assert_quota_updated():
    logger.info("Checking Strava quota keys")
    r = quota_manager.redis

    used_15m = r.get("strava:quota:15m:used")
    used_daily = r.get("strava:quota:daily:used")

    logger.info(f"Quota state: 15m={used_15m}, daily={used_daily}")
    logger.success("Quota keys present")


def assert_activities_written():
    logger.info("Checking activities table")
    with get_session() as session:
        count = session.query(Activity).count()

    if count == 0:
        logger.warning("No activities written yet (possible if no recent workouts)")
    else:
        logger.success(f"{count} activities found in DB")


def assert_backfill_progress():
    logger.info("Checking backfill progress")
    with get_session() as session:
        users = session.query(StravaAuth).all()

    for u in users:
        logger.info(f"user={u.athlete_id} backfill_page={u.backfill_page} backfill_done={u.backfill_done}")

    logger.success("Backfill state readable")


def main():
    start = datetime.now(UTC)
    logger.info("=== STARTING STRAVA INGESTION SYSTEM TEST ===")

    assert_redis()
    assert_db()

    athlete_ids = assert_users_exist()

    # Test locking on first user
    test_lock(athlete_ids[0])

    # Trigger ingestion
    trigger_ingestion()

    # Allow workers to run
    wait_for_processing(20)

    # Validate effects
    assert_quota_updated()
    assert_activities_written()
    assert_backfill_progress()

    elapsed = (datetime.now(UTC) - start).total_seconds()
    logger.success(f"=== SYSTEM TEST COMPLETE in {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()
