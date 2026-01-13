import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from sqlalchemy.exc import ProgrammingError

# Critical: Log immediately to catch import-time issues
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.info(">>> app.main import reached <<<")

from app.analytics.api import router as analytics_router
from app.api.activities.activities import router as activities_router
from app.api.admin.admin_activities import router as admin_activities_router
from app.api.admin.admin_ingestion_status import router as admin_ingestion_router
from app.api.admin.admin_memory import router as admin_memory_router
from app.api.admin.admin_pairing import router as admin_pairing_router
from app.api.admin.admin_retry import router as admin_retry_router
from app.api.admin.admin_sql import router as admin_sql_router
from app.api.admin.ingestion_reliability import router as ingestion_reliability_router
from app.api.auth.auth import router as auth_router
from app.api.auth.auth_google import router as auth_google_router
from app.api.auth.auth_strava import router as auth_strava_router
from app.api.coach.athletes import router as coach_athletes_router
from app.api.export.plan_export import router as export_router
from app.api.integrations.integrations_strava import router as integrations_strava_router
from app.api.intelligence.intelligence import router as intelligence_router
from app.api.intelligence.risks import router as risks_router
from app.api.onboarding.onboarding import router as onboarding_router
from app.api.strava.strava import router as strava_router
from app.api.training.manual_upload import router as manual_upload_router
from app.api.training.state import router as state_router
from app.api.training.training import router as training_router
from app.api.user.me import router as me_router
from app.calendar.api import router as calendar_router
from app.coach.api import router as coach_router
from app.coach.api_chat import router as coach_chat_router
from app.config.settings import settings
from app.core.conversation_id import conversation_id_middleware
from app.core.logger import setup_logger
from app.core.observe import init as observe_init
from app.db.models import Base
from app.db.schema_check import verify_schema
from app.db.session import get_engine
from app.domains.training_plan.template_loader import initialize_template_library_from_cache
from app.ingestion.api import router as ingestion_strava_router
from app.ingestion.scheduler import ingestion_tick
from app.ingestion.sync_scheduler import sync_tick
from app.internal.ai_ops.router import router as ai_ops_router
from app.internal.ops.latency import record_latency_ms
from app.internal.ops.router import router as ops_router
from app.internal.ops.summary import set_process_start_time
from app.internal.ops.traffic import record_request
from app.services.intelligence.scheduler import generate_daily_decisions_for_all_users
from app.services.intelligence.weekly_report_metrics import update_all_recent_weekly_reports_for_all_users
from app.webhooks.strava import router as webhooks_router
from app.workouts.routes import router as workouts_router
from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_source_default import migrate_activities_source_default
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_add_activity_tss import migrate_add_activity_tss
from scripts.migrate_add_athlete_id_to_planned_sessions import migrate_add_athlete_id_to_planned_sessions
from scripts.migrate_add_athlete_id_to_profiles import migrate_add_athlete_id_to_profiles
from scripts.migrate_add_conversation_summary import migrate_add_conversation_summary
from scripts.migrate_add_extracted_injury_attributes import migrate_add_extracted_injury_attributes
from scripts.migrate_add_extracted_race_attributes import migrate_add_extracted_race_attributes
from scripts.migrate_add_google_oauth_fields import migrate_add_google_oauth_fields
from scripts.migrate_add_imperial_profile_fields import migrate_add_imperial_profile_fields
from scripts.migrate_add_phase_to_planned_sessions import migrate_add_phase_to_planned_sessions
from scripts.migrate_add_planned_session_completion_fields import migrate_add_planned_session_completion_fields
from scripts.migrate_add_profile_health_fields import migrate_add_profile_health_fields
from scripts.migrate_add_session_order_to_planned_sessions import migrate_add_session_order_to_planned_sessions
from scripts.migrate_add_source_to_planned_sessions import migrate_add_source_to_planned_sessions
from scripts.migrate_add_streams_data import migrate_add_streams_data
from scripts.migrate_add_target_races import migrate_add_target_races
from scripts.migrate_add_user_is_active import migrate_add_user_is_active
from scripts.migrate_add_user_threshold_fields import migrate_add_user_threshold_fields
from scripts.migrate_athlete_id_to_string import migrate_athlete_id_to_string
from scripts.migrate_coach_messages_schema import migrate_coach_messages_schema
from scripts.migrate_create_workout_execution_tables import migrate_create_workout_execution_tables
from scripts.migrate_create_workout_exports_table import migrate_create_workout_exports_table
from scripts.migrate_create_workouts_tables import migrate_create_workouts_tables
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_daily_summary_user_id import migrate_daily_summary_user_id
from scripts.migrate_drop_activity_id import migrate_drop_activity_id
from scripts.migrate_drop_obsolete_activity_columns import migrate_drop_obsolete_activity_columns
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_llm_metadata_fields import migrate_llm_metadata_fields
from scripts.migrate_onboarding_data_fields import migrate_onboarding_data_fields
from scripts.migrate_strava_accounts import migrate_strava_accounts
from scripts.migrate_strava_accounts_sync_tracking import migrate_strava_accounts_sync_tracking
from scripts.migrate_user_auth_fields import migrate_user_auth_fields
from scripts.migrate_user_settings_fields import migrate_user_settings_fields

# Initialize logger with level from settings (defaults to INFO, can be overridden via LOG_LEVEL env var)
setup_logger(level=settings.log_level)

# Set OPENAI_API_KEY from settings if not already set in environment
# This ensures pydantic_ai and other libraries can find it
if settings.openai_api_key and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    logger.info("Set OPENAI_API_KEY from settings")
elif not settings.openai_api_key:
    logger.warning("OPENAI_API_KEY is not set. Coach features may not work.")


def initialize_database() -> None:
    """Initialize database tables and run migrations.

    This function is called during application startup (in lifespan),
    not at import time, to avoid Render deployment failures.
    """
    # Guard: Check DATABASE_URL is set
    if not settings.database_url:
        error_msg = "DATABASE_URL environment variable is not set"
        logging.error(f">>> {error_msg} <<<")
        raise RuntimeError(error_msg)

    try:
        print("DB INIT START", flush=True)
        logging.info(">>> initializing database <<<")
        logger.info("Starting database initialization...")

        # Initialize Observe SDK for LLM observability
        observe_init(
            api_key=settings.observe_api_key,
            enabled=settings.observe_enabled,
            sample_rate=settings.observe_sample_rate,
        )
        print("Observe SDK initialized", flush=True)

        # Ensure database tables exist
        logger.info("Ensuring database tables exist")
        db_engine = get_engine()
        Base.metadata.create_all(bind=db_engine)
        logger.info("Database tables verified")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)
        raise

    # Run migrations for derived tables
    logger.info("Running database migrations")
    migration_errors = []

    try:
        migrate_strava_accounts()
    except Exception as e:
        migration_errors.append(f"migrate_strava_accounts: {e}")
        logger.error(f"Migration failed: migrate_strava_accounts - {e}", exc_info=True)

    try:
        logger.info("Running migration: user authentication fields")
        migrate_user_auth_fields()
        logger.info("✓ Migration completed: user authentication fields")
    except Exception as e:
        migration_errors.append(f"migrate_user_auth_fields: {e}")
        logger.error(f"✗ Migration failed: migrate_user_auth_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: Google OAuth fields")
        migrate_add_google_oauth_fields()
        logger.info("✓ Migration completed: Google OAuth fields")
    except Exception as e:
        migration_errors.append(f"migrate_add_google_oauth_fields: {e}")
        logger.error(f"✗ Migration failed: migrate_add_google_oauth_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: add is_active to users table")
        migrate_add_user_is_active()
        logger.info("✓ Migration completed: add is_active to users table")
    except Exception as e:
        migration_errors.append(f"migrate_add_user_is_active: {e}")
        logger.error(f"✗ Migration failed: migrate_add_user_is_active - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles athlete_id column")
        migrate_add_athlete_id_to_profiles()
        logger.info("✓ Migration completed: athlete_profiles athlete_id column")
    except Exception as e:
        migration_errors.append(f"migrate_add_athlete_id_to_profiles: {e}")
        logger.error(f"✗ Migration failed: migrate_add_athlete_id_to_profiles - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles target_races column")
        migrate_add_target_races()
        logger.info("✓ Migration completed: athlete_profiles target_races column")
    except Exception as e:
        migration_errors.append(f"migrate_add_target_races: {e}")
        logger.error(f"✗ Migration failed: migrate_add_target_races - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles extracted_race_attributes column")
        migrate_add_extracted_race_attributes()
        logger.info("✓ Migration completed: athlete_profiles extracted_race_attributes column")
    except Exception as e:
        migration_errors.append(f"migrate_add_extracted_race_attributes: {e}")
        logger.error(f"✗ Migration failed: migrate_add_extracted_race_attributes - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles extracted_injury_attributes column")
        migrate_add_extracted_injury_attributes()
        logger.info("✓ Migration completed: athlete_profiles extracted_injury_attributes column")
    except Exception as e:
        migration_errors.append(f"migrate_add_extracted_injury_attributes: {e}")
        logger.error(f"✗ Migration failed: migrate_add_extracted_injury_attributes - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles health and constraint fields")
        migrate_add_profile_health_fields()
        logger.info("✓ Migration completed: athlete_profiles health and constraint fields")
    except Exception as e:
        migration_errors.append(f"migrate_add_profile_health_fields: {e}")
        logger.error(f"✗ Migration failed: migrate_add_profile_health_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: athlete_profiles imperial fields (height_in, weight_lbs)")
        migrate_add_imperial_profile_fields()
        logger.info("✓ Migration completed: athlete_profiles imperial fields")
    except Exception as e:
        migration_errors.append(f"migrate_add_imperial_profile_fields: {e}")
        logger.error(f"✗ Migration failed: migrate_add_imperial_profile_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: planned_sessions athlete_id column")
        migrate_add_athlete_id_to_planned_sessions()
        logger.info("✓ Migration completed: planned_sessions athlete_id column")
    except Exception as e:
        migration_errors.append(f"migrate_add_athlete_id_to_planned_sessions: {e}")
        logger.error(f"✗ Migration failed: migrate_add_athlete_id_to_planned_sessions - {e}", exc_info=True)

    try:
        logger.info("Running migration: planned_sessions completion tracking columns")
        migrate_add_planned_session_completion_fields()
        logger.info("✓ Migration completed: planned_sessions completion tracking columns")
    except Exception as e:
        migration_errors.append(f"migrate_add_planned_session_completion_fields: {e}")
        logger.error(f"✗ Migration failed: migrate_add_planned_session_completion_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: planned_sessions session_order column")
        migrate_add_session_order_to_planned_sessions()
        logger.info("✓ Migration completed: planned_sessions session_order column")
    except Exception as e:
        migration_errors.append(f"migrate_add_session_order_to_planned_sessions: {e}")
        logger.error(f"✗ Migration failed: migrate_add_session_order_to_planned_sessions - {e}", exc_info=True)

    try:
        logger.info("Running migration: planned_sessions phase column")
        migrate_add_phase_to_planned_sessions()
        logger.info("✓ Migration completed: planned_sessions phase column")
    except Exception as e:
        migration_errors.append(f"migrate_add_phase_to_planned_sessions: {e}")
        logger.error(f"✗ Migration failed: migrate_add_phase_to_planned_sessions - {e}", exc_info=True)

    try:
        logger.info("Running migration: planned_sessions source column")
        migrate_add_source_to_planned_sessions()
        logger.info("✓ Migration completed: planned_sessions source column")
    except Exception as e:
        migration_errors.append(f"migrate_add_source_to_planned_sessions: {e}")
        logger.error(f"✗ Migration failed: migrate_add_source_to_planned_sessions - {e}", exc_info=True)

    try:
        logger.info("Running migration: activities id column (integer to UUID)")
        migrate_activities_id_to_uuid()
        logger.info("✓ Migration completed: activities id column")
    except Exception as e:
        migration_errors.append(f"migrate_activities_id_to_uuid: {e}")
        logger.error(f"✗ Migration failed: migrate_activities_id_to_uuid - {e}", exc_info=True)

    try:
        migrate_activities_schema()
    except Exception as e:
        migration_errors.append(f"migrate_activities_schema: {e}")
        logger.error(f"Migration failed: migrate_activities_schema - {e}", exc_info=True)

    try:
        migrate_activities_user_id()
    except Exception as e:
        migration_errors.append(f"migrate_activities_user_id: {e}")
        logger.error(f"Migration failed: migrate_activities_user_id - {e}", exc_info=True)

    try:
        logger.info("Running migration: drop obsolete activity_id column")
        migrate_drop_activity_id()
        logger.info("✓ Migration completed: drop activity_id column")
    except Exception as e:
        migration_errors.append(f"migrate_drop_activity_id: {e}")
        logger.error(f"✗ Migration failed: migrate_drop_activity_id - {e}", exc_info=True)

    try:
        logger.info("Running migration: drop obsolete activity columns")
        migrate_drop_obsolete_activity_columns()
        logger.info("✓ Migration completed: drop obsolete activity columns")
    except Exception as e:
        migration_errors.append(f"migrate_drop_obsolete_activity_columns: {e}")
        logger.error(f"✗ Migration failed: migrate_drop_obsolete_activity_columns - {e}", exc_info=True)

    try:
        logger.info("Running migration: convert athlete_id to string")
        migrate_athlete_id_to_string()
        logger.info("✓ Migration completed: convert athlete_id to string")
    except Exception as e:
        migration_errors.append(f"migrate_athlete_id_to_string: {e}")
        logger.error(f"✗ Migration failed: migrate_athlete_id_to_string - {e}", exc_info=True)

    try:
        logger.info("Running migration: set source column default")
        migrate_activities_source_default()
        logger.info("✓ Migration completed: set source column default")
    except Exception as e:
        migration_errors.append(f"migrate_activities_source_default: {e}")
        logger.error(f"✗ Migration failed: migrate_activities_source_default - {e}", exc_info=True)

    try:
        migrate_daily_summary()
    except Exception as e:
        migration_errors.append(f"migrate_daily_summary: {e}")
        logger.error(f"Migration failed: migrate_daily_summary - {e}", exc_info=True)

    try:
        migrate_daily_summary_user_id()
    except Exception as e:
        migration_errors.append(f"migrate_daily_summary_user_id: {e}")
        logger.error(f"Migration failed: migrate_daily_summary_user_id - {e}", exc_info=True)

    try:
        migrate_history_cursor()
    except Exception as e:
        migration_errors.append(f"migrate_history_cursor: {e}")
        logger.error(f"Migration failed: migrate_history_cursor - {e}", exc_info=True)

    try:
        logger.info("Running migration: strava_accounts sync tracking columns")
        migrate_strava_accounts_sync_tracking()
        logger.info("✓ Migration completed: strava_accounts sync tracking columns")
    except Exception as e:
        migration_errors.append(f"migrate_strava_accounts_sync_tracking: {e}")
        logger.error(f"Migration failed: migrate_strava_accounts_sync_tracking - {e}", exc_info=True)

    try:
        logger.info("Running migration: coach_messages schema update")
        migrate_coach_messages_schema()
        logger.info("✓ Migration completed: coach_messages schema update")
    except Exception as e:
        migration_errors.append(f"migrate_coach_messages_schema: {e}")
        logger.error(f"Migration failed: migrate_coach_messages_schema - {e}", exc_info=True)

    try:
        logger.info("Running migration: onboarding data fields")
        migrate_onboarding_data_fields()
        logger.info("✓ Migration completed: onboarding data fields")
    except Exception as e:
        migration_errors.append(f"migrate_onboarding_data_fields: {e}")
        logger.error(f"Migration failed: migrate_onboarding_data_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: user_settings fields")
        migrate_user_settings_fields()
        logger.info("✓ Migration completed: user_settings fields")
    except Exception as e:
        migration_errors.append(f"migrate_user_settings_fields: {e}")
        logger.error(f"Migration failed: migrate_user_settings_fields - {e}", exc_info=True)

    # ⚠️ ONE-TIME MIGRATION: Add threshold fields (ftp_watts, threshold_pace_ms, threshold_hr)
    # This migration is idempotent (checks column existence before adding).
    # PRODUCTION NOTE: After first successful deployment, consider removing this from startup
    # and running migrations separately via: python scripts/run_migrations.py
    # This avoids coupling schema evolution to web process lifecycle.
    try:
        logger.info("Running migration: user_settings threshold configuration fields (ftp_watts, threshold_pace_ms, threshold_hr)")
        migrate_add_user_threshold_fields()
        logger.info("✓ Migration completed: user_settings threshold configuration fields")
    except Exception as e:
        # Non-blocking: app continues even if migration fails
        # Migration will be retried on next startup, or can be run manually
        migration_errors.append(f"migrate_add_user_threshold_fields: {e}")
        logger.error(
            f"Migration failed: migrate_add_user_threshold_fields - {e} "
            "(App will continue, but schema may be incomplete. Run manually: python scripts/migrate_add_user_threshold_fields.py)",
            exc_info=True,
        )

    try:
        logger.info("Running migration: LLM metadata fields and composite indexes")
        migrate_llm_metadata_fields()
        logger.info("✓ Migration completed: LLM metadata fields and composite indexes")
    except Exception as e:
        migration_errors.append(f"migrate_llm_metadata_fields: {e}")
        logger.error(f"Migration failed: migrate_llm_metadata_fields - {e}", exc_info=True)

    try:
        logger.info("Running migration: add streams_data column to activities")
        migrate_add_streams_data()
        logger.info("✓ Migration completed: add streams_data column to activities")
    except Exception as e:
        migration_errors.append(f"migrate_add_streams_data: {e}")
        logger.error(f"Migration failed: migrate_add_streams_data - {e}", exc_info=True)

    try:
        logger.info("Running migration: add tss and tss_version columns to activities")
        migrate_add_activity_tss()
        logger.info("✓ Migration completed: add tss and tss_version columns to activities")
    except Exception as e:
        migration_errors.append(f"migrate_add_activity_tss: {e}")
        logger.error(f"Migration failed: migrate_add_activity_tss - {e}", exc_info=True)

    try:
        logger.info("Running migration: conversation summary columns (B34)")
        migrate_add_conversation_summary()
        logger.info("✓ Migration completed: conversation summary columns (B34)")
    except Exception as e:
        migration_errors.append(f"migrate_add_conversation_summary: {e}")
        logger.error(f"Migration failed: migrate_add_conversation_summary - {e}", exc_info=True)

    try:
        logger.info("Running migration: create workouts tables")
        migrate_create_workouts_tables()
        logger.info("✓ Migration completed: create workouts tables")
    except Exception as e:
        migration_errors.append(f"migrate_create_workouts_tables: {e}")
        logger.error(f"Migration failed: migrate_create_workouts_tables - {e}", exc_info=True)

    try:
        logger.info("Running migration: create workout_exports table")
        migrate_create_workout_exports_table()
        logger.info("✓ Migration completed: create workout_exports table")
    except Exception as e:
        migration_errors.append(f"migrate_create_workout_exports_table: {e}")
        logger.error(f"Migration failed: migrate_create_workout_exports_table - {e}", exc_info=True)

    try:
        logger.info("Running migration: create workout execution and compliance tables")
        migrate_create_workout_execution_tables()
        logger.info("✓ Migration completed: create workout execution and compliance tables")
    except Exception as e:
        migration_errors.append(f"migrate_create_workout_execution_tables: {e}")
        logger.error(f"Migration failed: migrate_create_workout_execution_tables - {e}", exc_info=True)

    if migration_errors:
        logger.error(
            f"Some migrations failed ({len(migration_errors)} errors). "
            "The application will continue, but database schema may be incomplete. "
            "Run 'python scripts/run_migrations.py' manually to fix."
        )
    else:
        logger.info("Database migrations completed successfully")

    # Verify schema after migrations (fail fast if columns are missing)
    try:
        logger.info("Verifying database schema...")
        verify_schema()
        logger.info("✓ Database schema verification completed")
        logging.info(">>> database initialized <<<")
        print("DB INIT DONE", flush=True)
    except RuntimeError as e:
        logger.error(f"Schema verification failed: {e}")
        logger.error("Application startup aborted. Run migrations to fix schema issues.")
        print("DB INIT FAILED: Schema verification", flush=True)
        traceback.print_exc(file=sys.stdout)
        raise
    except Exception:
        print("DB INIT FAILED", flush=True)
        logger.exception("Database initialization failed")
        traceback.print_exc(file=sys.stdout)
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan - initialize database and start scheduler on startup.

    CRITICAL: This function must NEVER raise exceptions or block for long periods.
    Render's port scanner requires the server to bind and stay alive immediately.

    Heavy initialization (migrations, schedulers) is deferred to background tasks
    after the server has started and bound to the port.
    """
    logging.info(">>> lifespan startup begin <<<")
    print("LIFESPAN START", flush=True)

    # Initialize app state to track initialization status
    _app.state.db_ready = False
    _app.state.scheduler_ready = False
    _app.state.scheduler = None

    # Initialize ops metrics (process start time) - lightweight, safe to do here
    set_process_start_time(time.time())
    logger.info("[OPS] Initialized ops metrics tracking")

    # Attempt database initialization - but DO NOT raise on failure
    # This allows the server to start even if DB is temporarily unavailable
    try:
        initialize_database()
        _app.state.db_ready = True
        logging.info(">>> database initialized successfully <<<")
        print("DB INIT DONE", flush=True)
    except Exception as e:
        logging.exception(">>> lifespan: database init failed <<<")
        print(f"DB INIT FAILED: {e}", flush=True)
        logger.exception("Database initialization failed - running in degraded mode")
        traceback.print_exc(file=sys.stdout)
        # DO NOT raise - allow server to start in degraded mode
        _app.state.db_ready = False

    # Start scheduler - but only if DB is ready
    # Scheduler startup is relatively lightweight, but we still don't want to block
    if _app.state.db_ready:
        try:
            logging.info(">>> lifespan: starting scheduler <<<")
            scheduler = BackgroundScheduler()
            # Run background sync every 6 hours (Step 5: automated sync)
            scheduler.add_job(
                sync_tick,
                trigger=IntervalTrigger(hours=6),
                id="strava_background_sync",
                name="Strava Background Sync",
                replace_existing=True,
            )
            # Run ingestion tasks (including history backfill) every 30 minutes
            # Uses dynamic quota allocation: distributes available API quota across users
            # Automatically stops when quota is exhausted, redistributes as users complete
            # Maximizes throughput by using as much available quota as possible
            scheduler.add_job(
                ingestion_tick,
                trigger=IntervalTrigger(minutes=30),
                id="strava_ingestion_tick",
                name="Strava Ingestion Tick (History Backfill - Dynamic Quota)",
                replace_existing=True,
            )
            # Run daily decision generation overnight at 2 AM UTC
            scheduler.add_job(
                generate_daily_decisions_for_all_users,
                trigger=CronTrigger(hour=2, minute=0),
                id="daily_decision_generation",
                name="Daily Decision Generation",
                replace_existing=True,
            )
            # Run weekly report metrics update on Sundays at 3 AM UTC (after week ends)
            scheduler.add_job(
                update_all_recent_weekly_reports_for_all_users,
                trigger=CronTrigger(day_of_week=6, hour=3, minute=0),  # Sunday 3 AM UTC
                id="weekly_report_metrics_update",
                name="Weekly Report Metrics Update",
                replace_existing=True,
            )
            scheduler.start()
            _app.state.scheduler = scheduler
            _app.state.scheduler_ready = True
            logger.info("[SCHEDULER] Started automatic background sync scheduler (runs every 6 hours)")
            logger.info(
                "[SCHEDULER] Started ingestion tick scheduler "
                "(runs every 30 minutes, dynamic quota allocation for history backfill)"
            )
            logger.info("[SCHEDULER] Started daily decision generation scheduler (runs daily at 2 AM UTC)")
            logger.info("[SCHEDULER] Started weekly report metrics update scheduler (runs Sundays at 3 AM UTC)")
        except Exception as e:
            logger.exception("[SCHEDULER] Failed to start scheduler: {}", e)
            _app.state.scheduler_ready = False
            # Don't fail startup if scheduler fails
    else:
        logger.warning("[SCHEDULER] Skipping scheduler startup - database not ready")

    # Initialize template library (required for planner)
    # This must succeed - if it fails, the planner will not work
    try:
        logger.info("[TEMPLATE_LIBRARY] Initializing template library from cache")
        initialize_template_library_from_cache()
        logger.info("[TEMPLATE_LIBRARY] Template library initialized successfully")
    except Exception as e:
        logger.exception("[TEMPLATE_LIBRARY] Failed to initialize template library: {}", e)
        # This is a critical failure - planner will not work without templates
        # But we don't want to crash the entire server, so we log the error
        # The planner will fail with a clear error message if templates aren't loaded
        logger.error(
            "[TEMPLATE_LIBRARY] Planner will not work until template library is initialized. "
            "Run: python scripts/precompute_embeddings.py --templates"
        )

    # Yield control to FastAPI immediately - this allows the server to bind to the port
    # CRITICAL: Everything before yield runs during startup, everything after runs during shutdown
    logging.info(">>> lifespan: yielding to FastAPI (server will bind now) <<<")
    print("LIFESPAN YIELD", flush=True)
    await asyncio.sleep(0)
    yield

    # Shutdown code (runs when app is shutting down)
    logging.info(">>> lifespan: shutdown <<<")

    # Shutdown scheduler
    if _app.state.scheduler:
        try:
            _app.state.scheduler.shutdown()
            logger.info("[SCHEDULER] Stopped scheduler")
        except Exception as e:
            logger.exception("[SCHEDULER] Error during shutdown: {}", e)


logging.info(">>> creating FastAPI app <<<")
app = FastAPI(title="Virtus AI", lifespan=lifespan)
logging.info(">>> FastAPI app created <<<")


@app.on_event("startup")
async def deferred_heavy_init():
    """Run heavy initialization tasks after server has started and bound to port.

    This runs AFTER the server is up and serving requests, so it doesn't block
    Render's port detection. Heavy operations like initial sync ticks are deferred here.
    """
    # Wait a short moment to ensure server is fully up
    await asyncio.sleep(2)

    db_ready = getattr(app.state, "db_ready", False)
    if not db_ready:
        logger.warning("[DEFERRED INIT] Skipping deferred init - database not ready")
        return

    logging.info(">>> deferred_heavy_init: starting background tasks <<<")
    try:
        # Run initial sync tick (non-blocking - don't wait for completion)

        def run_sync_tick():
            try:
                sync_tick()
                logger.info("[DEFERRED INIT] Initial background sync tick completed")
            except Exception as e:
                logger.exception("[DEFERRED INIT] Initial background sync tick failed: {}", e)

        def run_ingestion_tick():
            try:
                ingestion_tick()
                logger.info("[DEFERRED INIT] Initial ingestion tick completed")
            except Exception as e:
                logger.exception("[DEFERRED INIT] Initial ingestion tick failed: {}", e)

        threading.Thread(target=run_sync_tick, daemon=True).start()
        threading.Thread(target=run_ingestion_tick, daemon=True).start()
        logging.info(">>> deferred_heavy_init: background tasks started <<<")
    except Exception as e:
        logger.exception("[DEFERRED INIT] Failed to start background tasks: {}", e)
        # Don't fail if deferred init fails


# Register conversation ID middleware FIRST (before CORS, auth, rate limiting, logging)
# This ensures conversation_id is available to all downstream middleware and handlers
@app.middleware("http")
async def conversation_id_middleware_wrapper(request: Request, call_next):
    """Wrapper to register conversation_id_middleware."""
    return await conversation_id_middleware(request, call_next)


# Request latency tracking middleware (after conversation_id, before CORS)
@app.middleware("http")
async def latency_tracking_middleware(request: Request, call_next):
    """Track request latency and traffic metrics."""
    start_time = time.time()

    try:
        response = await call_next(request)
    except Exception:
        # Record latency even on errors
        elapsed_ms = (time.time() - start_time) * 1000
        record_latency_ms(elapsed_ms)
        record_request()
        raise
    else:
        # Record latency and traffic on success
        elapsed_ms = (time.time() - start_time) * 1000
        record_latency_ms(elapsed_ms)
        record_request()
        return response


# Configure CORS
# Get allowed origins from environment variable or use defaults
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
if cors_origins_env:
    cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    cors_origins = [
        "https://pace-ai.onrender.com",  # Production frontend (legacy)
        "https://virtus-ai.onrender.com",  # Production frontend
        settings.frontend_url,  # Frontend URL from settings
        "http://localhost:5173",  # Local dev (Vite default)
        "http://localhost:3000",  # Local dev (alternative port)
        "http://localhost:8080",  # Local dev (alternative port)
        "http://localhost:8501",  # Streamlit default
        "capacitor://localhost",  # Capacitor iOS/Android
        "ionic://localhost",  # Ionic (alternative)
        "http://localhost",  # Android localhost
    ]

# Remove duplicates and filter out empty strings
cors_origins = list(set(filter(None, cors_origins)))
logger.info(f"[CORS] Configured allowed origins: {cors_origins}")

# CORS middleware must be added before routers to ensure it handles all requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-CSRFToken",
        "X-Conversation-Id",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
    ],
    expose_headers=["*"],  # Expose all headers to frontend
    max_age=3600,  # Cache preflight requests for 1 hour
)


# Register root and health endpoints first (before routers)
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def root():
    """Root endpoint - serves a simple HTML page with API information."""
    return """
    <html>
        <head>
            <title>Virtus AI</title>
        </head>
        <body>
            <h1>Virtus AI</h1>
            <p>Performance Intelligence & Coaching System</p>
            <h2>Available Endpoints:</h2>
            <ul>
                <li><a href="/docs">API Documentation (Swagger)</a></li>
                <li><a href="/redoc">API Documentation (ReDoc)</a></li>
                <li><a href="/health">Health Check</a></li>
                <li><a href="/auth/strava">Connect Strava</a></li>
            </ul>
        </body>
    </html>
    """


@app.get("/health")
def health():
    """Health check endpoint for monitoring and load balancers."""
    db_ready = getattr(app.state, "db_ready", False)
    scheduler_ready = getattr(app.state, "scheduler_ready", False)

    # Server is always "ok" - it's running and responding
    # But we report component status for debugging
    status = "ok"
    if not db_ready:
        status = "degraded"

    return {
        "status": status,
        "service": "Virtus AI Backend",
        "db_ready": db_ready,
        "scheduler_ready": scheduler_ready,
    }


@app.get("/healthz")
def healthz():
    """Hard health check endpoint (Kubernetes-style).

    Returns 200 if server is up and database is ready.
    Returns 503 if server is up but database is not ready (degraded mode).
    """
    db_ready = getattr(app.state, "db_ready", False)

    if db_ready:
        return {"status": "ok", "db_ready": True}
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "degraded", "db_ready": False, "message": "Database not ready"}
    )


@app.get("/debug/headers")
def debug_headers(request: Request):
    """Debug endpoint to check what headers are being received.

    This endpoint helps diagnose authentication issues by showing
    what headers the frontend is actually sending.
    """
    headers_dict = dict(request.headers)
    # Mask sensitive values
    safe_headers = {}
    for key, value in headers_dict.items():
        if key.lower() == "authorization":
            if value:
                safe_headers[key] = f"{value[:20]}... (masked)" if len(value) > 20 else f"{value[:10]}... (masked)"
            else:
                safe_headers[key] = "NOT PRESENT"
        else:
            safe_headers[key] = value

    return {
        "method": request.method,
        "path": request.url.path,
        "origin": request.headers.get("Origin", "Not set"),
        "authorization_header": "Present" if request.headers.get("Authorization") else "MISSING",
        "authorization_value": safe_headers.get("Authorization", "NOT PRESENT"),
        "all_headers": safe_headers,
        "header_count": len(headers_dict),
    }


# Register all API routers
app.include_router(activities_router)
app.include_router(admin_retry_router)
app.include_router(admin_ingestion_router)
app.include_router(admin_activities_router)
app.include_router(admin_memory_router)
app.include_router(admin_pairing_router)
app.include_router(admin_sql_router)
app.include_router(ingestion_reliability_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(auth_google_router)
app.include_router(auth_strava_router)
app.include_router(calendar_router)
app.include_router(coach_router)
app.include_router(coach_athletes_router)
app.include_router(coach_chat_router)
app.include_router(export_router)
app.include_router(ingestion_strava_router)
app.include_router(integrations_strava_router)
app.include_router(intelligence_router)
app.include_router(risks_router)
app.include_router(me_router)
app.include_router(onboarding_router)
app.include_router(strava_router)
app.include_router(manual_upload_router)
app.include_router(ops_router)
app.include_router(ai_ops_router)
app.include_router(state_router)
app.include_router(training_router)
app.include_router(webhooks_router)
app.include_router(workouts_router)

logger.info("FastAPI application initialized")
logger.info("Root endpoint available at: /")
logger.info("Health check available at: /health")
logger.info("API docs available at: /docs and /redoc")


@app.exception_handler(ProgrammingError)
def database_schema_error_handler(request: Request, exc: ProgrammingError):
    """Handle database schema mismatch errors with clear error messages.

    Catches SQLAlchemy ProgrammingError (e.g., missing columns) and provides
    helpful error messages instead of generic 500 errors.
    """
    error_msg = str(exc)
    # Use logger with explicit message formatting to avoid KeyError from SQL parameters in error message
    logger.error("Database schema error: %s", error_msg, exc_info=True)

    # Check if this is a missing column error
    if "does not exist" in error_msg.lower() or "undefinedcolumn" in error_msg.lower():
        logger.error(
            "Database schema mismatch detected. This usually means: "
            "1. Model was updated but migration wasn't run, or "
            "2. Migration failed to apply. "
            "Run: python scripts/validate_schema.py to check, "
            "then: python scripts/run_migrations.py to fix."
        )
        detail = (
            "Database schema mismatch: Model expects columns that don't exist in database. "
            "This is a deployment issue - migrations need to be run. "
            "Contact support with this error message."
        )
    else:
        detail = f"Database error: {error_msg}"

    # Get origin from request to add appropriate CORS headers
    origin = request.headers.get("origin")

    # Build response
    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail, "error_type": "database_schema_mismatch"},
    )

    # Add CORS headers if origin is in allowed list
    if origin and origin in cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Accept, Origin, X-Requested-With, X-Conversation-Id"
        )

    return response


@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled exceptions.

    Ensures CORS headers are added to error responses even when exceptions occur.
    """
    # Don't handle HTTPException - FastAPI handles those automatically with CORS
    if isinstance(exc, HTTPException):
        raise exc

    logger.exception(f"Unhandled exception: {exc}")

    # Get origin from request to add appropriate CORS headers
    origin = request.headers.get("origin")

    # Build response
    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

    # Add CORS headers if origin is in allowed list
    if origin and origin in cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Accept, Origin, X-Requested-With, X-Conversation-Id"
        )

    return response


@app.middleware("http")
async def ensure_cors_headers(request: Request, call_next):
    """Middleware to ensure CORS headers are present on all responses.

    This runs after CORS middleware and adds headers to responses that might
    have bypassed the CORS middleware (e.g., from exception handlers).
    """
    origin = request.headers.get("origin")

    response = await call_next(request)

    # Ensure CORS headers are present if origin is provided and allowed
    if origin and origin in cors_origins and "Access-Control-Allow-Origin" not in response.headers:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    logger.debug(f"Response: {response.status_code} for {request.method} {request.url.path}")
    return response
