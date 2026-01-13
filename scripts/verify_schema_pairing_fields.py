"""Verification script to check schema pairing fields.

This script verifies that required pairing columns exist:
- activities.planned_session_id
- planned_sessions.completed_activity_id

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
    """Verify that required pairing fields exist in schema.

    Returns:
        True if all required fields exist, False otherwise
    """
    logger.info("Verifying schema pairing fields")

    db = SessionLocal()
    all_ok = True
    try:
        if _is_postgresql():
            # Check activities.planned_session_id
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'activities'
                    AND column_name = 'planned_session_id'
                    """,
                ),
            ).fetchone()

            if result:
                logger.info("✅ activities.planned_session_id exists")
            else:
                logger.error("❌ activities.planned_session_id MISSING")
                all_ok = False

            # Check planned_sessions.completed_activity_id
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'planned_sessions'
                    AND column_name = 'completed_activity_id'
                    """,
                ),
            ).fetchone()

            if result:
                logger.info("✅ planned_sessions.completed_activity_id exists")
            else:
                logger.error("❌ planned_sessions.completed_activity_id MISSING")
                all_ok = False

            # List all activities columns for sanity check
            logger.info("\nAll activities columns:")
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'activities'
                    ORDER BY column_name
                    """,
                ),
            ).fetchall()
            for row in result:
                logger.info(f"  - {row[0]}")

            # Check for required columns
            column_names = [row[0] for row in result]
            required_columns = ["planned_session_id", "workout_id", "tss", "tss_version"]
            logger.info("\nRequired columns check:")
            for col in required_columns:
                if col in column_names:
                    logger.info(f"  ✅ {col}")
                else:
                    logger.error(f"  ❌ {col} MISSING")
                    all_ok = False

        else:
            logger.warning("SQLite detected - using PRAGMA table_info")
            # SQLite check
            result = db.execute(text("PRAGMA table_info(activities)")).fetchall()
            activity_columns = [col[1] for col in result]

            if "planned_session_id" in activity_columns:
                logger.info("✅ activities.planned_session_id exists")
            else:
                logger.error("❌ activities.planned_session_id MISSING")
                all_ok = False

            result = db.execute(text("PRAGMA table_info(planned_sessions)")).fetchall()
            planned_columns = [col[1] for col in result]

            if "completed_activity_id" in planned_columns:
                logger.info("✅ planned_sessions.completed_activity_id exists")
            else:
                logger.error("❌ planned_sessions.completed_activity_id MISSING")
                all_ok = False

        if all_ok:
            logger.info("\n✅ All required pairing fields exist in schema")
        else:
            logger.error("\n❌ Some required pairing fields are missing")
            logger.error("Run: python scripts/migrate_fix_schema_pairing_fields.py")
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
