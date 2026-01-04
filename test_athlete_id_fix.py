#!/usr/bin/env python3
"""Quick test script to verify athlete_id is being saved correctly."""

from loguru import logger
from sqlalchemy import select

from app.state.db import get_session
from app.state.models import Activity, StravaAuth


def test_athlete_id_fix():
    """Test that activities have athlete_id populated."""
    logger.info("Testing athlete_id fix...")

    with get_session() as session:
        # Check if we have any activities
        total = session.query(Activity).count()
        logger.info(f"Total activities in database: {total}")

        if total == 0:
            logger.warning("No activities found in database.")
            logger.info("To test the fix, you need to trigger a Strava sync:")
            logger.info("1. Connect Strava: http://localhost:8000/auth/strava")
            logger.info("2. Trigger sync: curl -X POST http://localhost:8000/strava/sync")
            return

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
        elif without_athlete_id > 0:
            logger.error(f"❌ FAILURE: {without_athlete_id} activities are missing athlete_id")
            logger.error("The fix is NOT working - activities are being saved without athlete_id")
        else:
            logger.warning("⚠️  No activities found to verify")


if __name__ == "__main__":
    test_athlete_id_fix()
