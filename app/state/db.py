from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings


def _validate_postgresql_driver() -> None:
    """Validate PostgreSQL driver is installed when using PostgreSQL.

    Must actually import psycopg2 (not just check spec) because SQLAlchemy
    will try to import it when creating the engine.
    """
    if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
        try:
            import psycopg2  # type: ignore[reportMissingModuleSource]  # noqa: F401, PLC0415

            logger.info("PostgreSQL driver (psycopg2) is available")
        except ImportError as e:
            logger.error(
                "⚠️ CRITICAL: PostgreSQL driver (psycopg2) is not installed!\n"
                "Install it with: pip install psycopg2-binary\n"
                "Or add 'psycopg2-binary>=2.9.9' to requirements.txt"
            )
            raise ImportError("PostgreSQL driver required. Install with: pip install psycopg2-binary") from e


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

# Validate PostgreSQL driver BEFORE creating engine (SQLAlchemy imports psycopg2 on engine creation)
_is_postgresql = "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()
if _is_postgresql:
    _validate_postgresql_driver()  # Must happen before create_engine() for PostgreSQL
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
    """Get database session context manager.

    Handles database errors vs HTTP exceptions separately:
    - HTTPException: Re-raised without logging (expected API responses)
    - Other exceptions: Logged as database errors and rolled back
    """
    logger.debug("Creating new database session")
    session = SessionLocal()
    try:
        logger.debug(
            f"Yielding session: dirty={len(session.dirty)}, new={len(session.new)}, "
            f"deleted={len(session.deleted)}"
        )
        yield session
        
        logger.debug(
            f"Before commit: dirty={len(session.dirty)}, new={len(session.new)}, "
            f"deleted={len(session.deleted)}"
        )
        
        if session.dirty:
            logger.debug(f"Session has {len(session.dirty)} dirty objects: {[str(obj) for obj in list(session.dirty)[:3]]}")
        if session.new:
            logger.debug(f"Session has {len(session.new)} new objects: {[str(obj) for obj in list(session.new)[:3]]}")
        
        logger.debug("Calling session.commit()")
        session.commit()
        logger.debug("Database session committed successfully")
    except HTTPException:
        # HTTPException is an expected API response, not a database error
        # Re-raise without logging or rolling back (no DB transaction to rollback)
        logger.debug("HTTPException in session, rolling back")
        session.rollback()
        raise
    except KeyError as e:
        # KeyError during commit - this is unusual, log extensively
        logger.error(
            f"Database session KeyError during commit, rolling back: {e}. "
            f"Error args: {e.args}, session state: "
            f"dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}"
        )
        if session.new:
            for obj in list(session.new)[:3]:
                logger.error(f"New object in session: {type(obj).__name__}, id={getattr(obj, 'id', 'NO_ID')}, raw_json_type={type(getattr(obj, 'raw_json', None))}")
                if hasattr(obj, 'raw_json') and isinstance(obj.raw_json, dict):
                    logger.error(f"raw_json keys: {list(obj.raw_json.keys())[:20]}")
        logger.error("Full KeyError traceback:", exc_info=True)
        session.rollback()
        raise
    except Exception as e:
        # Actual database error - log and rollback
        logger.error(
            f"Database session error during commit, rolling back: {e}. "
            f"Error type: {type(e).__name__}, Error args: {e.args}, session state: "
            f"dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}"
        )
        if session.new:
            for obj in list(session.new)[:3]:
                logger.error(f"New object in session: {type(obj).__name__}, id={getattr(obj, 'id', 'NO_ID')}")
        logger.error("Full exception traceback:", exc_info=True)
        session.rollback()
        raise
    finally:
        logger.debug("Closing database session")
        session.close()
        logger.debug("Database session closed")
