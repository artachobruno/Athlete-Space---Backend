import asyncio
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger

from app.analytics.api import router as analytics_router
from app.api.activities.activities import router as activities_router
from app.api.admin.admin_activities import router as admin_activities_router
from app.api.admin.admin_ingestion_status import router as admin_ingestion_router
from app.api.admin.admin_retry import router as admin_retry_router
from app.api.auth.auth import router as auth_router
from app.api.auth.auth_strava import router as auth_strava_router
from app.api.integrations.integrations_strava import router as integrations_strava_router
from app.api.intelligence.intelligence import router as intelligence_router
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
from app.db.session import engine
from app.ingestion.api import router as ingestion_strava_router
from app.ingestion.sync_scheduler import sync_tick
from app.services.intelligence.scheduler import generate_daily_decisions_for_all_users
from app.webhooks.strava import router as webhooks_router
from scripts.migrate_activities_id_to_uuid import migrate_activities_id_to_uuid
from scripts.migrate_activities_schema import migrate_activities_schema
from scripts.migrate_activities_source_default import migrate_activities_source_default
from scripts.migrate_activities_user_id import migrate_activities_user_id
from scripts.migrate_athlete_id_to_string import migrate_athlete_id_to_string
from scripts.migrate_daily_summary import migrate_daily_summary
from scripts.migrate_drop_activity_id import migrate_drop_activity_id
from scripts.migrate_drop_obsolete_activity_columns import migrate_drop_obsolete_activity_columns
from scripts.migrate_history_cursor import migrate_history_cursor
from scripts.migrate_strava_accounts import migrate_strava_accounts

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

if migration_errors:
    logger.error(
        f"Some migrations failed ({len(migration_errors)} errors). "
        "The application will continue, but database schema may be incomplete. "
        "Run 'python scripts/run_migrations.py' manually to fix."
    )
else:
    logger.info("Database migrations completed successfully")


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
    # Run daily decision generation overnight at 2 AM UTC
    scheduler.add_job(
        generate_daily_decisions_for_all_users,
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_decision_generation",
        name="Daily Decision Generation",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[SCHEDULER] Started automatic background sync scheduler (runs every 6 hours)")
    logger.info("[SCHEDULER] Started daily decision generation scheduler (runs daily at 2 AM UTC)")

    # Run initial sync tick
    try:
        sync_tick()
        logger.info("[SCHEDULER] Initial background sync tick completed")
    except Exception as e:
        logger.exception("[SCHEDULER] Initial background sync tick failed: {}", e)

    # Yield control to FastAPI (use await to satisfy async requirement)
    await asyncio.sleep(0)
    yield

    # Shutdown scheduler
    scheduler.shutdown()
    logger.info("[SCHEDULER] Stopped ingestion scheduler")


app = FastAPI(title="Virtus AI", lifespan=lifespan)

# Configure CORS
cors_origins = [
    "https://pace-ai.onrender.com",  # Production frontend
    settings.frontend_url,  # Frontend URL from settings
    "http://localhost:5173",  # Local dev (Vite default)
    "http://localhost:3000",  # Local dev (alternative port)
]
# Remove duplicates and filter out empty strings
cors_origins = list(set(filter(None, cors_origins)))
logger.info(f"[CORS] Configured allowed origins: {cors_origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    expose_headers=["Location", "Content-Type", "Authorization"],
)

app.include_router(activities_router)
app.include_router(admin_retry_router)
app.include_router(admin_ingestion_router)
app.include_router(admin_activities_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(auth_strava_router)
app.include_router(calendar_router)
app.include_router(coach_router)
app.include_router(coach_chat_router)
app.include_router(ingestion_strava_router)
app.include_router(integrations_strava_router)
app.include_router(intelligence_router)
app.include_router(me_router)
app.include_router(strava_router)
app.include_router(state_router)
app.include_router(training_router)
app.include_router(webhooks_router)

logger.info("FastAPI application initialized")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests."""
    logger.debug(f"Request: {request.method} {request.url.path}")
    response = await call_next(request)
    logger.debug(f"Response: {response.status_code} for {request.method} {request.url.path}")
    return response


@app.get("/", response_class=HTMLResponse)
def root():
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
                <li><a href="/auth/strava">Connect Strava</a></li>
            </ul>
        </body>
    </html>
    """
