from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings


def _validate_postgresql_driver() -> None:
    """Validate PostgreSQL driver is installed when using PostgreSQL.

    Must actually import psycopg2 (not just check spec) because SQLAlchemy
    will try to import it when creating the engine.
    """
    if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
        try:
            import psycopg2

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
        with _get_engine().connect() as conn:
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


# Lazy initialization to avoid import-time database connections (Render deployment requirement)
_engine = None
_SessionLocal = None


def _get_engine():
    """Get or create the database engine (lazy initialization).

    This function ensures the engine is only created when first accessed,
    not at import time, to avoid Render deployment failures.
    """
    global _engine
    if _engine is None:
        logger.info(f"Initializing database engine: {settings.database_url}")

        # Validate PostgreSQL driver BEFORE creating engine (SQLAlchemy imports psycopg2 on engine creation)
        is_postgresql = "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()
        if is_postgresql:
            _validate_postgresql_driver()  # Must happen before create_engine() for PostgreSQL
            logger.info("Using PostgreSQL database (production-ready)")
        else:
            logger.warning("Using SQLite database (local development only)")

        # Create engine with appropriate connection args
        connect_args = {}
        if "sqlite" in settings.database_url.lower():
            connect_args = {"check_same_thread": False}
        elif is_postgresql:
            # PostgreSQL connection pooling settings
            connect_args = {
                "connect_timeout": 10,
                "application_name": "virtus-ai",
            }

        _engine = create_engine(
            settings.database_url,
            connect_args=connect_args,
            echo=False,  # Set to True for SQL query logging
            pool_pre_ping=True,  # Verify connections before using (important for cloud DBs)
            pool_recycle=3600,  # Recycle connections after 1 hour
        )
        logger.info("Database engine initialized")
    return _engine


def get_engine():
    """Get or create the database engine (public API).

    This is a public wrapper around _get_engine() to avoid importing
    private functions from external modules.
    """
    return _get_engine()


def _get_session_local():
    """Get or create the session factory (lazy initialization)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_get_engine())
        logger.info("Database session factory initialized")
    return _SessionLocal


# For backward compatibility, use __getattr__ to provide lazy access
# This allows existing code like `from app.db.session import engine` to work
def __getattr__(name: str):
    """Lazy attribute access for backward compatibility."""
    if name == "engine":
        return _get_engine()
    if name == "SessionLocal":
        return _get_session_local()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _handle_session_commit(session: Session) -> None:
    """Handle session commit with logging."""
    with suppress(Exception):
        logger.debug(f"Before commit: dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}")
    if session.dirty:
        with suppress(Exception):
            logger.debug(f"Session has {len(session.dirty)} dirty objects: {[str(obj) for obj in list(session.dirty)[:3]]}")
    if session.new:
        with suppress(Exception):
            logger.debug(f"Session has {len(session.new)} new objects: {[str(obj) for obj in list(session.new)[:3]]}")
    # Only commit if there are changes to avoid unnecessary commits
    if session.dirty or session.new or session.deleted:
        with suppress(Exception):
            logger.debug("Calling session.commit()")
        session.commit()
        with suppress(Exception):
            logger.debug("Database session committed successfully")
    else:
        with suppress(Exception):
            logger.debug("No changes to commit, skipping commit")


def get_db() -> Generator[Session, None, None]:
    """Get database session for FastAPI dependencies.

    This is a plain generator function (NOT a context manager) that FastAPI
    can use directly with Depends(). FastAPI will handle cleanup automatically.

    For non-FastAPI code that needs a context manager, use get_session() instead.

    Yields:
        Session: SQLAlchemy database session

    Example:
        @router.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    logger.debug("Creating new database session (FastAPI dependency)")
    session = _get_session_local()()
    try:
        logger.debug(f"Yielding session: dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}")
        yield session
    finally:
        logger.debug("Closing database session (FastAPI dependency)")
        session.close()
        logger.debug("Database session closed")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Get database session context manager.

    Handles database errors vs HTTP exceptions separately:
    - HTTPException: Re-raised without logging (expected API responses)
    - NoTrainingDataError: Re-raised without logging (business logic error, not DB error)
    - Other exceptions: Logged as database errors and rolled back

    For FastAPI route dependencies, use get_db() instead.
    """
    logger.debug("Creating new database session")
    session = _get_session_local()()
    try:
        logger.debug(f"Yielding session: dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}")
        yield session
        _handle_session_commit(session)
    except HTTPException:
        logger.debug("HTTPException in session, rolling back")
        session.rollback()
        raise
    except Exception as e:
        # Check if this is a business logic error (not a database error)
        # Import here to avoid circular imports
        from app.state.errors import NoTrainingDataError

        if isinstance(e, NoTrainingDataError):
            logger.debug("NoTrainingDataError in session, rolling back (business logic error, not DB error)")
            session.rollback()
            raise
        logger.error(
            f"Database session error during commit, rolling back: {e}. "
            f"Error type: {type(e).__name__}, Error args: {e.args}, session state: "
            f"dirty={len(session.dirty)}, new={len(session.new)}, deleted={len(session.deleted)}"
        )
        if session.new:
            for obj in list(session.new)[:3]:
                logger.error(f"New object in session: {type(obj).__name__}, id={getattr(obj, 'id', 'NO_ID')}")
        logger.exception("Full exception traceback:")
        session.rollback()
        raise
    finally:
        logger.debug("Closing database session")
        session.close()
        logger.debug("Database session closed")
