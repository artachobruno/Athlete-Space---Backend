import asyncio
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from sqlalchemy.exc import ProgrammingError

from app.analytics.api import router as analytics_router
from app.api.activities.activities import router as activities_router
from app.api.admin.admin_activities import router as admin_activities_router
from app.api.admin.admin_ingestion_status import router as admin_ingestion_router
from app.api.admin.admin_retry import router as admin_retry_router
from app.api.admin.ingestion_reliability import router as ingestion_reliability_router
from app.api.auth.auth import router as auth_router
from app.api.auth.auth_strava import router as auth_strava_router
from app.api.integrations.integrations_strava import router as integrations_strava_router
from app.api.intelligence.intelligence import router as intelligence_router
from app.api.intelligence.risks import router as risks_router
from app.api.onboarding.onboarding import router as onboarding_router
from app.api.strava.strava import router as strava_router
from app.api.training.state import router as state_router
from app.api.training.training import router as training_router
from app.api.user.me import router as me_router
from app.calendar.api import router as calendar_router
from app.coach.api import router as coach_router
from app.coach.api_chat import router as coach_chat_router
from app.config.settings import settings
from app.core.logger import setup_logger
from app.db.models import Base
from app.db.schema_check import verify_schema
from app.db.session import engine
from app.ingestion.api import router as ingestion_strava_router
from app.ingestion.scheduler import ingestion_tick
from app.ingestion.sync_scheduler import sync_tick
from app.services.intelligence.scheduler import generate_daily_decisions_for_all_users
from app.services.intelligence.weekly_report_metrics import update_all_recent_weekly_reports_for_all_users
from app.webhooks.strava import router as webhooks_router
from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_source_default import migrate_activities_source_default
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_add_athlete_id_to_planned_sessions import migrate_add_athlete_id_to_planned_sessions
from scripts.migrate_add_athlete_id_to_profiles import migrate_add_athlete_id_to_profiles
from scripts.migrate_add_extracted_injury_attributes import migrate_add_extracted_injury_attributes
from scripts.migrate_add_extracted_race_attributes import migrate_add_extracted_race_attributes
from scripts.migrate_add_profile_health_fields import migrate_add_profile_health_fields
from scripts.migrate_add_streams_data import migrate_add_streams_data
from scripts.migrate_add_target_races import migrate_add_target_races
from scripts.migrate_athlete_id_to_string import migrate_athlete_id_to_string
from scripts.migrate_coach_messages_schema import migrate_coach_messages_schema
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_drop_activity_id import migrate_drop_activity_id
from scripts.migrate_drop_obsolete_activity_columns import migrate_drop_obsolete_activity_columns
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_llm_metadata_fields import migrate_llm_metadata_fields
from scripts.migrate_onboarding_data_fields import migrate_onboarding_data_fields
from scripts.migrate_strava_accounts import migrate_strava_accounts
from scripts.migrate_strava_accounts_sync_tracking import migrate_strava_accounts_sync_tracking
from scripts.migrate_user_auth_fields import migrate_user_auth_fields

# Initialize logger with level from settings (defaults to INFO, can be overridden via LOG_LEVEL env var)
setup_logger(level=settings.log_level)

# Set OPENAI_API_KEY from settings if not already set in environment
# This ensures pydantic_ai and other libraries can find it
if settings.openai_api_key and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    logger.info("Set OPENAI_API_KEY from settings")
elif not settings.openai_api_key:
    logger.warning("OPENAI_API_KEY is not set. Coach features may not work.")

# Ensure database tables exist
logger.info("Ensuring database tables exist")
Base.metadata.create_all(bind=engine)
logger.info("Database tables verified")

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
    logger.info("Running migration: planned_sessions athlete_id column")
    migrate_add_athlete_id_to_planned_sessions()
    logger.info("✓ Migration completed: planned_sessions athlete_id column")
except Exception as e:
    migration_errors.append(f"migrate_add_athlete_id_to_planned_sessions: {e}")
    logger.error(f"✗ Migration failed: migrate_add_athlete_id_to_planned_sessions - {e}", exc_info=True)

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
    from scripts.migrate_daily_summary_user_id import migrate_daily_summary_user_id

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
except RuntimeError as e:
    logger.error(f"Schema verification failed: {e}")
    logger.error("Application startup aborted. Run migrations to fix schema issues.")
    raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan - start scheduler on startup.

    Note: FastAPI requires async for lifespan context manager,
    even if no await operations are used.
    """
    # Start scheduler
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
    logger.info("[SCHEDULER] Started automatic background sync scheduler (runs every 6 hours)")
    logger.info("[SCHEDULER] Started ingestion tick scheduler (runs every 30 minutes, dynamic quota allocation for history backfill)")
    logger.info("[SCHEDULER] Started daily decision generation scheduler (runs daily at 2 AM UTC)")
    logger.info("[SCHEDULER] Started weekly report metrics update scheduler (runs Sundays at 3 AM UTC)")

    # Run initial sync tick
    try:
        sync_tick()
        logger.info("[SCHEDULER] Initial background sync tick completed")
    except Exception as e:
        logger.exception("[SCHEDULER] Initial background sync tick failed: {}", e)

    # Run initial ingestion tick to start history backfill
    try:
        ingestion_tick()
        logger.info("[SCHEDULER] Initial ingestion tick completed")
    except Exception as e:
        logger.exception("[SCHEDULER] Initial ingestion tick failed: {}", e)

    # Yield control to FastAPI (use await to satisfy async requirement)
    await asyncio.sleep(0)
    yield

    # Shutdown scheduler
    scheduler.shutdown()
    logger.info("[SCHEDULER] Stopped ingestion scheduler")


app = FastAPI(title="Virtus AI", lifespan=lifespan)

# Configure CORS
# Get allowed origins from environment variable or use defaults
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
if cors_origins_env:
    cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    cors_origins = [
        "https://pace-ai.onrender.com",  # Production frontend
        settings.frontend_url,  # Frontend URL from settings
        "http://localhost:5173",  # Local dev (Vite default)
        "http://localhost:3000",  # Local dev (alternative port)
        "http://localhost:8080",  # Local dev (alternative port)
        "http://localhost:8501",  # Streamlit default
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
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
    ],
    expose_headers=["*"],  # Expose all headers to frontend
    max_age=3600,  # Cache preflight requests for 1 hour
)


# Register root and health endpoints first (before routers)
@app.get("/", response_class=HTMLResponse)
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
    return {"status": "ok", "service": "Virtus AI Backend"}


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
app.include_router(ingestion_reliability_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(auth_strava_router)
app.include_router(calendar_router)
app.include_router(coach_router)
app.include_router(coach_chat_router)
app.include_router(ingestion_strava_router)
app.include_router(integrations_strava_router)
app.include_router(intelligence_router)
app.include_router(risks_router)
app.include_router(me_router)
app.include_router(onboarding_router)
app.include_router(strava_router)
app.include_router(state_router)
app.include_router(training_router)
app.include_router(webhooks_router)

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
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept, Origin, X-Requested-With"

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
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept, Origin, X-Requested-With"

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
