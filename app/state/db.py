from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings

logger.info(f"Initializing database engine: {settings.database_url}")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=False,  # Set to True for SQL query logging
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

logger.info("Database engine and session factory initialized")


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
