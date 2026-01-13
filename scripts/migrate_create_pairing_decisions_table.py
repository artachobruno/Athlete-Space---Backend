"""Migration script to create pairing_decisions audit table.

This migration creates the pairing_decisions table for auditing all pairing decisions:
- id: UUID primary key
- user_id: User ID
- planned_session_id: Planned session ID (nullable)
- activity_id: Activity ID (nullable)
- decision: Decision type (paired, rejected, manual_unpair)
- duration_diff_pct: Duration difference percentage (nullable)
- reason: Reason for decision
- created_at: Timestamp

Usage:
    From project root:
    python scripts/migrate_create_pairing_decisions_table.py

    Or as a module:
    python -m scripts.migrate_create_pairing_decisions_table
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _table_exists(db, table_name: str) -> bool:
    """Check if a table exists.

    Args:
        db: Database session
        table_name: Name of the table

    Returns:
        True if table exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = :table_name
                """,
            ),
            {"table_name": table_name},
        ).fetchone()
        return result is not None
    result = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    ).fetchone()
    return result is not None


def migrate_create_pairing_decisions_table() -> None:
    """Create pairing_decisions audit table."""
    logger.info("Starting migration: create pairing_decisions table")

    db = SessionLocal()
    try:
        if _table_exists(db, "pairing_decisions"):
            logger.info("Table pairing_decisions already exists, skipping")
        else:
            logger.info("Creating pairing_decisions table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        CREATE TABLE pairing_decisions (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            user_id VARCHAR NOT NULL,
                            planned_session_id VARCHAR,
                            activity_id VARCHAR,
                            decision TEXT NOT NULL,
                            duration_diff_pct DOUBLE PRECISION,
                            reason TEXT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT NOW()
                        )
                        """,
                    ),
                )
                # Add indexes for common queries
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_user_id
                        ON pairing_decisions(user_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_planned_session_id
                        ON pairing_decisions(planned_session_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_activity_id
                        ON pairing_decisions(activity_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_created_at
                        ON pairing_decisions(created_at)
                        """,
                    ),
                )
            else:
                # SQLite
                db.execute(
                    text(
                        """
                        CREATE TABLE pairing_decisions (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            planned_session_id VARCHAR,
                            activity_id VARCHAR,
                            decision TEXT NOT NULL,
                            duration_diff_pct REAL,
                            reason TEXT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """,
                    ),
                )
                # Add indexes for common queries
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_user_id
                        ON pairing_decisions(user_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_planned_session_id
                        ON pairing_decisions(planned_session_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_activity_id
                        ON pairing_decisions(activity_id)
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_pairing_decisions_created_at
                        ON pairing_decisions(created_at)
                        """,
                    ),
                )

        db.commit()
        logger.info("Successfully created pairing_decisions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_create_pairing_decisions_table()
    logger.info("Migration completed successfully")
