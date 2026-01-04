#!/usr/bin/env python3
"""Test athlete_id fix with polling to wait for sync completion."""

import time

from loguru import logger
from sqlalchemy import select

from app.state.db import get_session
from app.state.models import Activity, StravaAuth


def test_athlete_id_fix_with_wait(max_wait_seconds: int = 60):
    """Test that activities have athlete_id populated, waiting for sync if needed."""
    logger.info("Testing athlete_id fix...")

    # Wait for activities to appear (sync is async)
    wait_interval = 2  # Check every 2 seconds
    waited = 0

    while waited < max_wait_seconds:
        with get_session() as session:
            total = session.query(Activity).count()

            if total > 0:
                logger.info(f"Found {total} activities! Verifying athlete_id...")
                break
            logger.info(f"No activities yet (waited {waited}s)... checking again in {wait_interval}s")
            time.sleep(wait_interval)
            waited += wait_interval

    with get_session() as session:
        # Check if we have any activities
        total = session.query(Activity).count()
        logger.info(f"Total activities in database: {total}")

        if total == 0:
            logger.warning("No activities found in database after waiting.")
            logger.info("The sync may still be running. Check server logs for errors.")
            logger.info("You can also check sync progress: curl http://localhost:8000/strava/sync-progress")
            return None

        # Check for activities without athlete_id (should be 0)
        without_athlete_id = session.query(Activity).filter(Activity.athlete_id.is_(None)).count()

        # Check for activities with athlete_id
        with_athlete_id = session.query(Activity).filter(Activity.athlete_id.isnot(None)).count()

        logger.info(f"Activities with athlete_id: {with_athlete_id}")
        logger.info(f"Activities without athlete_id: {without_athlete_id}")

        # Get a sample activity
        sample = session.query(Activity).first()
        if sample:
            logger.info("\nSample activity:")
            logger.info(f"  ID: {sample.id}")
            logger.info(f"  athlete_id: {sample.athlete_id}")
            logger.info(f"  user_id: {sample.user_id}")
            logger.info(f"  strava_activity_id: {sample.strava_activity_id}")
            logger.info(f"  type: {sample.type}")
            logger.info(f"  start_time: {sample.start_time}")

        # Verify athlete_id matches StravaAuth
        result = session.execute(select(StravaAuth)).first()
        if result:
            expected_athlete_id = result[0].athlete_id
            logger.info(f"\nExpected athlete_id from StravaAuth: {expected_athlete_id}")

            if sample and sample.athlete_id == expected_athlete_id:
                logger.success("✅ athlete_id matches StravaAuth!")
            elif sample:
                logger.warning(f"⚠️  athlete_id mismatch: activity has {sample.athlete_id}, expected {expected_athlete_id}")

        # Final verdict
        if without_athlete_id == 0 and with_athlete_id > 0:
            logger.success("✅ SUCCESS: All activities have athlete_id!")
            logger.info("The fix is working correctly!")
            return True
        if without_athlete_id > 0:
            logger.error(f"❌ FAILURE: {without_athlete_id} activities are missing athlete_id")
            logger.error("The fix is NOT working - activities are being saved without athlete_id")
            return False
        logger.warning("⚠️  No activities found to verify")
        return None


if __name__ == "__main__":
    test_athlete_id_fix_with_wait(max_wait_seconds=60)
