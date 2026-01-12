# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# WARNING: This will DROP the existing activities table and recreate it.
# Only use this for local development/testing databases!
# Keep for historical reference and potential database recovery scenarios
"""Recreate activities table with correct schema.

WARNING: This will DROP the existing activities table and recreate it.
Only use this for local development/testing databases!
"""

from loguru import logger
from sqlalchemy import text

from app.db.models import Base
from app.db.session import get_engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    engine = get_engine()
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def recreate_activities_table() -> None:
    """Drop and recreate activities table with correct schema."""
    logger.warning("⚠️  WARNING: This will DROP the existing activities table!")
    logger.warning("⚠️  Only use this for local development databases!")

    engine = get_engine()
    with engine.begin() as conn:
        # Check if table exists
        if _is_postgresql():
            result = conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = 'activities'
                    )
                """)
            )
            table_exists = result.scalar() is True
        else:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='activities'"))
            table_exists = result.fetchone() is not None

        if table_exists:
            # Check if table has data
            result = conn.execute(text("SELECT COUNT(*) FROM activities"))
            count = result.scalar()
            if count is not None and count > 0:
                logger.warning(f"⚠️  Table has {count} rows - they will be lost!")
                logger.warning("⚠️  Consider backing up the database first!")

            logger.info("Dropping existing activities table...")
            conn.execute(text("DROP TABLE IF EXISTS activities"))
            logger.info("✓ Dropped existing table")

        # Create new table using SQLAlchemy models
        logger.info("Creating new activities table from models...")
        Base.metadata.create_all(bind=get_engine(), tables=[Base.metadata.tables["activities"]])
        logger.info("✓ Created new activities table with correct schema")

        # Verify the schema
        if _is_postgresql():
            result = conn.execute(
                text("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'activities'
                    ORDER BY ordinal_position
                """)
            )
            columns = result.fetchall()
        else:
            result = conn.execute(text("PRAGMA table_info(activities)"))
            columns = [(row[1], row[2]) for row in result.fetchall()]  # name, type

        logger.info("New schema columns:")
        for col_name, col_type in columns:
            logger.info(f"  - {col_name}: {col_type}")

        # Check for required columns
        column_names = [col[0] for col in columns]
        required_columns = ["id", "user_id", "athlete_id", "strava_activity_id"]
        missing = [col for col in required_columns if col not in column_names]

        if missing:
            logger.error(f"❌ Missing required columns: {missing}")
            raise ValueError(f"Table creation failed - missing columns: {missing}")
        logger.success("✅ All required columns present (id, user_id, athlete_id, strava_activity_id)")


if __name__ == "__main__":
    recreate_activities_table()
