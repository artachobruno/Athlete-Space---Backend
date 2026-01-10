"""Migration script to add conversation summary columns to conversation_progress table (B34).

This migration adds:
- conversation_summary: JSONB column for structured summary (facts, preferences, goals, open_threads)
- summary_updated_at: TIMESTAMP for tracking when summary was last updated

These columns enable long-term memory storage via structured conversation summarization.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_add_conversation_summary() -> None:
    """Add conversation_summary and summary_updated_at columns to conversation_progress table if they don't exist."""
    logger.info("Starting conversation summary migration for conversation_progress table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if columns already exist
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = 'conversation_progress'
                            AND column_name = 'conversation_summary'
                        )
                        """
                    )
                )
                summary_column_exists = result.scalar() is True

                result = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = 'conversation_progress'
                            AND column_name = 'summary_updated_at'
                        )
                        """
                    )
                )
                timestamp_column_exists = result.scalar() is True
            else:
                result = conn.execute(text("PRAGMA table_info(conversation_progress)"))
                columns = [row[1] for row in result.fetchall()]
                summary_column_exists = "conversation_summary" in columns
                timestamp_column_exists = "summary_updated_at" in columns

            if summary_column_exists and timestamp_column_exists:
                logger.info(
                    "conversation_summary and summary_updated_at columns already exist in conversation_progress table, skipping migration"
                )
                return

            logger.info("Adding conversation summary columns to conversation_progress table...")

            # Add conversation_summary column (nullable, JSON/JSONB)
            if not summary_column_exists:
                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN conversation_summary JSONB NULL
                            """
                        )
                    )
                else:
                    # SQLite doesn't have JSONB, use TEXT
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN conversation_summary TEXT NULL
                            """
                        )
                    )
                logger.info("Added conversation_summary column to conversation_progress table")

            # Add summary_updated_at column (nullable, TIMESTAMP)
            if not timestamp_column_exists:
                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN summary_updated_at TIMESTAMP NULL
                            """
                        )
                    )
                else:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN summary_updated_at TIMESTAMP NULL
                            """
                        )
                    )
                logger.info("Added summary_updated_at column to conversation_progress table")

            logger.info("Conversation summary migration completed successfully")

        except Exception as e:
            logger.error(f"Error during conversation summary migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_conversation_summary()
