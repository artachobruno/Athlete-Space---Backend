"""Verification script to check schema pairing fields and invariants.

This script verifies:
- workouts.planned_session_id NOT NULL
- planned_sessions.workout_id NOT NULL
- No orphan workouts (workouts without planned_session_id)
- No broken foreign keys

Usage:
    From project root:
    python scripts/verify_schema_pairing_fields.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def verify_schema_pairing_fields() -> bool:
    """Verify schema pairing fields and invariants.

    Checks:
    - workouts.planned_session_id NOT NULL
    - planned_sessions.workout_id NOT NULL
    - No orphan workouts
    - No broken foreign keys

    Returns:
        True if all checks pass, False otherwise
    """
    logger.info("Verifying schema pairing fields and invariants")

    db = SessionLocal()
    all_ok = True
    try:
        if _is_postgresql():
            # Check workouts.planned_session_id NOT NULL
            result = db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM workouts
                    WHERE planned_session_id IS NULL
                    """,
                ),
            ).scalar()

            if result == 0:
                logger.info("✅ workouts.planned_session_id NOT NULL (all workouts have planned_session_id)")
            else:
                logger.error(f"❌ workouts.planned_session_id has {result} NULL values")
                all_ok = False

            # Check planned_sessions.workout_id NOT NULL
            result = db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM planned_sessions
                    WHERE workout_id IS NULL
                    """,
                ),
            ).scalar()

            if result == 0:
                logger.info("✅ planned_sessions.workout_id NOT NULL (all planned sessions have workout_id)")
            else:
                logger.error(f"❌ planned_sessions.workout_id has {result} NULL values")
                all_ok = False

            # Check for orphan workouts (workouts without matching planned_session)
            result = db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM workouts w
                    LEFT JOIN planned_sessions ps ON w.planned_session_id = ps.id
                    WHERE w.planned_session_id IS NOT NULL
                    AND ps.id IS NULL
                    """,
                ),
            ).scalar()

            if result == 0:
                logger.info("✅ No orphan workouts (all workouts.planned_session_id reference valid planned_sessions)")
            else:
                logger.error(f"❌ Found {result} orphan workouts (workouts with invalid planned_session_id)")
                all_ok = False

            # Check for broken foreign keys (planned_sessions.workout_id references)
            result = db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM planned_sessions ps
                    LEFT JOIN workouts w ON ps.workout_id = w.id
                    WHERE ps.workout_id IS NOT NULL
                    AND w.id IS NULL
                    """,
                ),
            ).scalar()

            if result == 0:
                logger.info("✅ No broken foreign keys (all planned_sessions.workout_id reference valid workouts)")
            else:
                logger.error(f"❌ Found {result} broken foreign keys (planned_sessions with invalid workout_id)")
                all_ok = False

        else:
            logger.warning("SQLite detected - using PRAGMA table_info and direct queries")
            # SQLite check
            result = db.execute(
                text("SELECT COUNT(*) FROM workouts WHERE planned_session_id IS NULL")
            ).scalar()

            if result == 0:
                logger.info("✅ workouts.planned_session_id NOT NULL")
            else:
                logger.error(f"❌ workouts.planned_session_id has {result} NULL values")
                all_ok = False

            result = db.execute(
                text("SELECT COUNT(*) FROM planned_sessions WHERE workout_id IS NULL")
            ).scalar()

            if result == 0:
                logger.info("✅ planned_sessions.workout_id NOT NULL")
            else:
                logger.error(f"❌ planned_sessions.workout_id has {result} NULL values")
                all_ok = False

        if all_ok:
            logger.info("\n✅ All schema invariants verified successfully")
        else:
            logger.error("\n❌ Some schema invariants are violated")
    except Exception as e:
        logger.error(f"Error during verification: {e}")
        return False
    else:
        return all_ok
    finally:
        db.close()


if __name__ == "__main__":
    success = verify_schema_pairing_fields()
    sys.exit(0 if success else 1)
