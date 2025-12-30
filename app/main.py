from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from app.api.coach_chat import router as coach_chat_router
from app.api.state import router as state_router
from app.api.strava import router as strava_router
from app.core.logger import setup_logger
from app.state.db import engine
from app.state.models import Base

# Initialize logger
setup_logger(level="INFO")

# Ensure database tables exist
logger.info("Ensuring database tables exist")
Base.metadata.create_all(bind=engine)
logger.info("Database tables verified")

app = FastAPI(title="Virtus AI")

app.include_router(coach_chat_router)
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
