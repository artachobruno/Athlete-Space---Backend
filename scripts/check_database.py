#!/usr/bin/env python3
"""Check database connection and configuration.

Usage:
    python scripts/check_database.py
"""

from __future__ import annotations

import sys

from loguru import logger
from sqlalchemy import inspect, text

from app.core.settings import settings
from app.state.db import engine


def _check_database_type() -> tuple[bool, bool]:
    """Check and log database type.

    Returns:
        Tuple of (is_postgresql, is_sqlite)
    """
    is_postgresql = "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()
    is_sqlite = "sqlite" in settings.database_url.lower()

    if is_postgresql:
        logger.info("✅ Database type: PostgreSQL (production-ready)")
    elif is_sqlite:
        logger.warning("⚠️ Database type: SQLite (local development only)")
        logger.warning("⚠️ Data will be LOST on container rebuilds!")
    else:
        logger.error(f"❌ Unknown database type: {settings.database_url}")

    return is_postgresql, is_sqlite


def _test_connection() -> bool:
    """Test database connection.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        logger.info("Testing database connection...")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Database connection test successful")
    except Exception as e:
        logger.error(f"❌ Connection test failed: {e}")
        return False
    else:
        return True


def _check_tables() -> bool:
    """Check database tables and record counts.

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info("Checking database tables...")
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"Found {len(tables)} table(s): {', '.join(tables) if tables else 'none'}")

        # Check for required tables
        required_tables = ["strava_auth", "activities"]
        missing_tables = [t for t in required_tables if t not in tables]
        if missing_tables:
            logger.warning(f"⚠️ Missing required tables: {', '.join(missing_tables)}")
            logger.info("Run: python scripts/init_db.py to create tables")
        else:
            logger.info("✅ All required tables exist")
        # Check table record counts
        _log_table_counts(tables)
    except Exception as e:
        logger.error(f"❌ Error checking tables: {e}")
        return False
    else:
        return True


def _log_table_counts(tables: list[str]) -> None:
    """Log record counts for existing tables."""
    if "strava_auth" in tables:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM strava_auth"))
            count = result.scalar()
            logger.info(f"📊 StravaAuth records: {count}")

    if "activities" in tables:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM activities"))
            count = result.scalar()
            logger.info(f"📊 Activity records: {count}")


def _check_postgresql_info() -> None:
    """Check PostgreSQL-specific information."""
    try:
        with engine.connect() as conn:
            # Check PostgreSQL version
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            if version and isinstance(version, str):
                logger.info(f"📊 PostgreSQL version: {version.split(',')[0]}")
            else:
                logger.info("📊 PostgreSQL version: (unknown)")

            # Check connection count
            result = conn.execute(text("SELECT count(*) FROM pg_stat_activity"))
            connections = result.scalar()
            if connections is not None:
                logger.info(f"📊 Active connections: {connections}")
            else:
                logger.info("📊 Active connections: (unknown)")
    except Exception as e:
        logger.warning(f"⚠️ Could not get PostgreSQL info: {e}")


def check_database() -> int:
    """Check database connection and configuration.

    Returns:
        0 if successful, 1 if errors found
    """
    logger.info("🔍 Checking database configuration...")
    logger.info(f"Database URL: {settings.database_url}")

    # Check database type
    is_postgresql, is_sqlite = _check_database_type()
    if not is_postgresql and not is_sqlite:
        return 1

    # Test connection
    if not _test_connection():
        return 1

    # Check tables
    if not _check_tables():
        return 1

    # PostgreSQL-specific checks
    if is_postgresql:
        _check_postgresql_info()

    logger.info("✅ Database check completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(check_database())
