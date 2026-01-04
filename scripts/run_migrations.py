"""Run all database migrations in the correct order.

This script runs all necessary migrations to ensure the database schema
matches the current model definitions.

Run this script manually if migrations fail on application startup,
or to apply migrations to a production database.
"""

import sys

from loguru import logger

from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_strava_accounts import migrate_strava_accounts


def run_all_migrations() -> None:
    """Run all database migrations in the correct order."""
    logger.info("Starting database migrations...")

    migrations = [
        ("strava_accounts table", migrate_strava_accounts),
        ("activities id column (integer to UUID)", migrate_activities_id_to_uuid),
        ("activities schema (add missing columns)", migrate_activities_schema),
        ("activities user_id column", migrate_activities_user_id),
        ("daily_summary tables", migrate_daily_summary),
        ("history cursor fields", migrate_history_cursor),
    ]

    for migration_name, migration_func in migrations:
        try:
            logger.info(f"Running migration: {migration_name}")
            migration_func()
            logger.info(f"✓ Migration completed: {migration_name}")
        except Exception as e:
            logger.error(f"✗ Migration failed: {migration_name}")
            logger.error(f"Error: {e}", exc_info=True)
            raise

    logger.info("All migrations completed successfully!")


if __name__ == "__main__":
    try:
        run_all_migrations()
    except Exception as e:
        logger.error(f"Migration process failed: {e}")
        sys.exit(1)
