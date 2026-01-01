from __future__ import annotations

import importlib.util
from collections.abc import Generator
from contextlib import contextmanager, suppress

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings


def _validate_postgresql_driver() -> None:
    """Validate PostgreSQL driver is installed when using PostgreSQL."""
    if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
        spec = importlib.util.find_spec("psycopg2")
        if spec is None:
            logger.error(
                "⚠️ CRITICAL: PostgreSQL driver (psycopg2) is not installed!\n"
                "Install it with: pip install psycopg2-binary\n"
                "Or add 'psycopg2-binary>=2.9.9' to requirements.txt"
            )
            raise ImportError("PostgreSQL driver required. Install with: pip install psycopg2-binary") from None
        logger.info("PostgreSQL driver (psycopg2) is available")


def _test_database_connection() -> None:
    """Test database connection on startup."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Database connection test successful")
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {e}")
        logger.error(
            "Check your DATABASE_URL environment variable and ensure:\n"
            "1. Database server is running\n"
            "2. Connection string is correct\n"
            "3. Network access is allowed (for cloud databases)"
        )
        raise


logger.info(f"Initializing database engine: {settings.database_url}")

# Validate PostgreSQL driver if using PostgreSQL
_is_postgresql = "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()
if _is_postgresql:
    _validate_postgresql_driver()
    logger.info("Using PostgreSQL database (production-ready)")
else:
    logger.warning("Using SQLite database (local development only)")

# Create engine with appropriate connection args
connect_args = {}
if "sqlite" in settings.database_url.lower():
    connect_args = {"check_same_thread": False}
elif _is_postgresql:
    # PostgreSQL connection pooling settings
    connect_args = {
        "connect_timeout": 10,
        "application_name": "virtus-ai",
    }

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,  # Set to True for SQL query logging
    pool_pre_ping=True,  # Verify connections before using (important for cloud DBs)
    pool_recycle=3600,  # Recycle connections after 1 hour
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

logger.info("Database engine and session factory initialized")

# Test connection on module load
with suppress(Exception):
    # Don't fail on import, but log the error
    # The app will fail on first DB operation if connection is bad
    _test_database_connection()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Get database session context manager."""
    logger.debug("Creating new database session")
    session = SessionLocal()
    try:
        yield session
        session.commit()
        logger.debug("Database session committed")
    except Exception as e:
        logger.error(f"Database session error, rolling back: {e}")
        session.rollback()
        raise
    finally:
        session.close()
        logger.debug("Database session closed")
