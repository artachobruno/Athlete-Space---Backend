import asyncio
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from app.api.admin_activities import router as admin_activities_router
from app.api.admin_ingestion_status import router as admin_ingestion_router
from app.api.admin_retry import router as admin_retry_router
from app.api.coach_chat import router as coach_chat_router
from app.api.me import router as me_router
from app.api.state import router as state_router
from app.api.strava import router as strava_router
from app.core.logger import setup_logger
from app.core.settings import settings
from app.ingestion.scheduler import ingestion_tick
from app.state.db import engine
from app.state.models import Base
from scripts.migrate_daily_summary import migrate_daily_summary

# Initialize logger
setup_logger(level="INFO")

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
try:
    migrate_daily_summary()
    logger.info("Database migrations completed successfully")
except Exception as e:
    logger.error(f"Migration error (non-fatal): {e}", exc_info=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan - start scheduler on startup.

    Note: FastAPI requires async for lifespan context manager,
    even if no await operations are used.
    """
    # Start scheduler
    scheduler = BackgroundScheduler()
    # Run ingestion every 5 minutes to continue backfill and get new activities
    scheduler.add_job(
        ingestion_tick,
        trigger=IntervalTrigger(minutes=5),
        id="strava_ingestion",
        name="Strava Ingestion Scheduler",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[SCHEDULER] Started automatic ingestion scheduler (runs every 5 minutes)")

    # Run initial ingestion tick
    try:
        ingestion_tick()
        logger.info("[SCHEDULER] Initial ingestion tick completed")
    except Exception as e:
        logger.error(f"[SCHEDULER] Initial ingestion tick failed: {e}", exc_info=True)

    # Yield control to FastAPI (use await to satisfy async requirement)
    await asyncio.sleep(0)
    yield

    # Shutdown scheduler
    scheduler.shutdown()
    logger.info("[SCHEDULER] Stopped ingestion scheduler")


app = FastAPI(title="Virtus AI", lifespan=lifespan)


app.include_router(admin_retry_router)
app.include_router(admin_ingestion_router)
app.include_router(admin_activities_router)
app.include_router(coach_chat_router)
app.include_router(me_router)
app.include_router(strava_router)
app.include_router(state_router)

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
                <li><a href="/strava/connect">Connect Strava</a></li>
            </ul>
        </body>
    </html>
    """
