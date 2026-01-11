"""Migration script to create calendar_sessions table.

This migration creates the calendar_sessions table for materializing completed activities
into calendar sessions. The table enforces one calendar session per activity via a unique
constraint on activity_id.

Supports both SQLite and PostgreSQL.
"""

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists (database-agnostic)."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return result.scalar() is True
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def _unique_constraint_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a unique constraint exists on a column."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.table_schema = 'public'
                AND tc.table_name = :table_name
                AND tc.constraint_type = 'UNIQUE'
                AND ccu.column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    # SQLite: Check if column has UNIQUE constraint in table_info
    result = conn.execute(text(f"PRAGMA index_list({table_name})"))
    indexes = result.fetchall()
    for index in indexes:
        # Check if this is a unique index on the column
        index_name = index[1]
        index_info = conn.execute(text(f"PRAGMA index_info({index_name})"))
        columns_in_index = [row[1] for row in index_info.fetchall()]
        if column_name in columns_in_index and index[2]:  # index[2] is unique flag
            return True
    return False


def migrate_calendar_sessions() -> None:
    """Create calendar_sessions table if it doesn't exist.

    The table will be created by SQLAlchemy's Base.metadata.create_all() if it doesn't exist.
    This migration ensures the table structure is correct and adds the unique constraint
    on activity_id if the table exists but the constraint is missing.

    Supports both SQLite and PostgreSQL.
    """
    logger.info("Starting calendar_sessions migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.connect() as conn:
        table_exists = _table_exists(conn, "calendar_sessions")

    if not table_exists:
        logger.info("calendar_sessions table does not exist. It will be created by Base.metadata.create_all()")
        # The table will be created by SQLAlchemy ORM with all constraints
        return

    logger.info("calendar_sessions table exists, checking constraints...")

    with engine.begin() as conn:
        # Check if activity_id column exists
        if not _column_exists(conn, "calendar_sessions", "activity_id"):
            logger.info("Adding activity_id column to calendar_sessions table...")
            float_type = "DOUBLE PRECISION" if _is_postgresql() else "REAL"
            string_type = "VARCHAR" if _is_postgresql() else "VARCHAR"
            datetime_type = "TIMESTAMP" if _is_postgresql() else "DATETIME"

            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE calendar_sessions
                        ADD COLUMN activity_id VARCHAR NOT NULL DEFAULT ''
                        """
                    )
                )
                # Remove default after adding
                conn.execute(
                    text(
                        """
                        ALTER TABLE calendar_sessions
                        ALTER COLUMN activity_id DROP DEFAULT
                        """
                    )
                )
            else:
                # SQLite: ALTER TABLE ADD COLUMN doesn't support NOT NULL without default in some versions
                conn.execute(
                    text(
                        """
                        ALTER TABLE calendar_sessions
                        ADD COLUMN activity_id VARCHAR
                        """
                    )
                )
            logger.info("✓ Added activity_id column")

        # Check if unique constraint exists on activity_id
        if not _unique_constraint_exists(conn, "calendar_sessions", "activity_id"):
            logger.info("Adding unique constraint on activity_id column...")
            if _is_postgresql():
                # Check for existing duplicate activity_ids before adding constraint
                result = conn.execute(
                    text(
                        """
                        SELECT activity_id, COUNT(*) as cnt
                        FROM calendar_sessions
                        WHERE activity_id IS NOT NULL AND activity_id != ''
                        GROUP BY activity_id
                        HAVING COUNT(*) > 1
                        """
                    )
                )
                duplicates = result.fetchall()
                if duplicates:
                    logger.warning(f"Found {len(duplicates)} duplicate activity_ids. Removing duplicates...")
                    # Keep the first occurrence of each activity_id
                    for dup in duplicates:
                        activity_id_to_keep = dup[0]
                        conn.execute(
                            text(
                                """
                                DELETE FROM calendar_sessions
                                WHERE id NOT IN (
                                    SELECT id FROM calendar_sessions
                                    WHERE activity_id = :activity_id
                                    LIMIT 1
                                )
                                AND activity_id = :activity_id
                                """
                            ),
                            {"activity_id": activity_id_to_keep},
                        )

                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_sessions_activity_id
                        ON calendar_sessions (activity_id)
                        """
                    )
                )
            else:
                # SQLite: Create unique index
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_sessions_activity_id
                        ON calendar_sessions (activity_id)
                        """
                    )
                )
            logger.info("✓ Added unique constraint on activity_id")
        else:
            logger.info("Unique constraint on activity_id already exists, skipping")

    logger.info(f"Migration complete: calendar_sessions table ready ({db_type})")


if __name__ == "__main__":
    migrate_calendar_sessions()
