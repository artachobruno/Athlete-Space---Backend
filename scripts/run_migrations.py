"""Run all database migrations in the correct order.

This script runs all necessary migrations to ensure the database schema
matches the current model definitions.

Run this script manually if migrations fail on application startup,
or to apply migrations to a production database.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_add_activity_tss import migrate_add_activity_tss
from scripts.migrate_add_athlete_id_to_planned_sessions import migrate_add_athlete_id_to_planned_sessions
from scripts.migrate_add_athlete_id_to_profiles import migrate_add_athlete_id_to_profiles
from scripts.migrate_add_extracted_injury_attributes import migrate_add_extracted_injury_attributes
from scripts.migrate_add_extracted_race_attributes import migrate_add_extracted_race_attributes
from scripts.migrate_add_planned_session_completion_fields import migrate_add_planned_session_completion_fields
from scripts.migrate_add_profile_health_fields import migrate_add_profile_health_fields
from scripts.migrate_add_streams_data import migrate_add_streams_data
from scripts.migrate_add_target_races import migrate_add_target_races
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_strava_accounts import migrate_strava_accounts
from scripts.migrate_strava_accounts_sync_tracking import migrate_strava_accounts_sync_tracking
from scripts.migrate_user_auth_fields import migrate_user_auth_fields


def run_all_migrations() -> None:
    """Run all database migrations in the correct order."""
    logger.info("Starting database migrations...")

    migrations = [
        ("strava_accounts table", migrate_strava_accounts),
        ("user authentication fields", migrate_user_auth_fields),
        ("athlete_profiles athlete_id column", migrate_add_athlete_id_to_profiles),
        ("athlete_profiles target_races column", migrate_add_target_races),
        ("athlete_profiles extracted_race_attributes column", migrate_add_extracted_race_attributes),
        ("athlete_profiles extracted_injury_attributes column", migrate_add_extracted_injury_attributes),
        ("athlete_profiles health and constraint fields", migrate_add_profile_health_fields),
        ("planned_sessions athlete_id column", migrate_add_athlete_id_to_planned_sessions),
        ("planned_sessions completion tracking columns", migrate_add_planned_session_completion_fields),
        ("activities id column (integer to UUID)", migrate_activities_id_to_uuid),
        ("activities schema (add missing columns)", migrate_activities_schema),
        ("activities user_id column", migrate_activities_user_id),
        ("activities streams_data column", migrate_add_streams_data),
        ("activities tss and tss_version columns", migrate_add_activity_tss),
        ("daily_summary tables", migrate_daily_summary),
        ("history cursor fields", migrate_history_cursor),
        ("strava_accounts sync tracking columns", migrate_strava_accounts_sync_tracking),
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
