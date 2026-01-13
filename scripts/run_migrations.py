"""Run all database migrations in the correct order.

This script runs all necessary migrations to ensure the database schema
matches the current model definitions.

Run this script manually if migrations fail on application startup,
or to apply migrations to a production database.
"""

import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger

from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_add_activity_effort_fields import migrate_add_activity_effort_fields
from scripts.migrate_add_activity_tss import migrate_add_activity_tss
from scripts.migrate_add_athlete_id_to_planned_sessions import migrate_add_athlete_id_to_planned_sessions
from scripts.migrate_add_athlete_id_to_profiles import migrate_add_athlete_id_to_profiles
from scripts.migrate_add_conversation_summaries_table import migrate_add_conversation_summaries_table
from scripts.migrate_add_conversation_summary import migrate_add_conversation_summary
from scripts.migrate_add_extracted_injury_attributes import migrate_add_extracted_injury_attributes
from scripts.migrate_add_extracted_race_attributes import migrate_add_extracted_race_attributes
from scripts.migrate_add_google_oauth_fields import migrate_add_google_oauth_fields
from scripts.migrate_add_imperial_profile_fields import migrate_add_imperial_profile_fields
from scripts.migrate_add_llm_interpretation_fields import migrate_add_llm_interpretation_fields
from scripts.migrate_add_planned_session_completion_fields import migrate_add_planned_session_completion_fields
from scripts.migrate_add_profile_health_fields import migrate_add_profile_health_fields
from scripts.migrate_add_source_to_planned_sessions import migrate_add_source_to_planned_sessions
from scripts.migrate_add_streams_data import migrate_add_streams_data
from scripts.migrate_add_target_races import migrate_add_target_races
from scripts.migrate_add_user_is_active import migrate_add_user_is_active
from scripts.migrate_add_user_threshold_fields import migrate_add_user_threshold_fields
from scripts.migrate_add_workout_id_to_activities import migrate_add_workout_id_to_activities
from scripts.migrate_add_workout_id_to_planned_sessions import migrate_add_workout_id_to_planned_sessions
from scripts.migrate_calendar_sessions import migrate_calendar_sessions
from scripts.migrate_create_workout_execution_tables import migrate_create_workout_execution_tables
from scripts.migrate_create_workout_exports_table import migrate_create_workout_exports_table
from scripts.migrate_create_workouts_tables import migrate_create_workouts_tables
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_onboarding_data_fields import migrate_onboarding_data_fields
from scripts.migrate_set_workout_id_not_null import migrate_set_workout_id_not_null
from scripts.migrate_strava_accounts import migrate_strava_accounts
from scripts.migrate_strava_accounts_sync_tracking import migrate_strava_accounts_sync_tracking
from scripts.migrate_user_auth_fields import migrate_user_auth_fields
from scripts.migrate_user_settings_fields import migrate_user_settings_fields


def run_all_migrations() -> None:
    """Run all database migrations in the correct order."""
    logger.info("Starting database migrations...")

    migrations = [
        ("strava_accounts table", migrate_strava_accounts),
        ("user authentication fields", migrate_user_auth_fields),
        ("Google OAuth fields", migrate_add_google_oauth_fields),
        ("user is_active field", migrate_add_user_is_active),
        ("athlete_profiles athlete_id column", migrate_add_athlete_id_to_profiles),
        ("athlete_profiles target_races column", migrate_add_target_races),
        ("athlete_profiles extracted_race_attributes column", migrate_add_extracted_race_attributes),
        ("athlete_profiles extracted_injury_attributes column", migrate_add_extracted_injury_attributes),
        ("athlete_profiles health and constraint fields", migrate_add_profile_health_fields),
        ("athlete_profiles imperial fields (height_in, weight_lbs)", migrate_add_imperial_profile_fields),
        ("onboarding data fields (onboarding_completed, etc.)", migrate_onboarding_data_fields),
        ("planned_sessions athlete_id column", migrate_add_athlete_id_to_planned_sessions),
        ("planned_sessions completion tracking columns", migrate_add_planned_session_completion_fields),
        ("planned_sessions source column", migrate_add_source_to_planned_sessions),
        ("activities id column (integer to UUID)", migrate_activities_id_to_uuid),
        ("activities schema (add missing columns)", migrate_activities_schema),
        ("activities user_id column", migrate_activities_user_id),
        ("activities streams_data column", migrate_add_streams_data),
        ("activities tss and tss_version columns", migrate_add_activity_tss),
        ("activities effort computation fields (normalized_power, effort_source, intensity_factor)", migrate_add_activity_effort_fields),
        ("user_settings threshold configuration fields (ftp_watts, threshold_pace_ms, threshold_hr)", migrate_add_user_threshold_fields),
        ("calendar_sessions table", migrate_calendar_sessions),
        ("daily_summary tables", migrate_daily_summary),
        ("history cursor fields", migrate_history_cursor),
        ("strava_accounts sync tracking columns", migrate_strava_accounts_sync_tracking),
        ("user_settings fields (units, timezone, notifications_enabled)", migrate_user_settings_fields),
        ("conversation summary columns (B34)", migrate_add_conversation_summary),
        ("conversation_summaries table (B35)", migrate_add_conversation_summaries_table),
        ("workouts and workout_steps tables", migrate_create_workouts_tables),
        ("workout_exports table", migrate_create_workout_exports_table),
        ("workout execution and compliance tables", migrate_create_workout_execution_tables),
        ("planned_sessions workout_id column", migrate_add_workout_id_to_planned_sessions),
        ("activities workout_id column", migrate_add_workout_id_to_activities),
        ("LLM interpretation fields", migrate_add_llm_interpretation_fields),
        # NOTE: migrate_set_workout_id_not_null should be run AFTER backfill_workouts.py completes
        # It is NOT included here - run it manually after backfilling data
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
