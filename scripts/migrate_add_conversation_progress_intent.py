"""Migration script to add intent column to conversation_progress table.

This migration adds:
- intent: String column (nullable, indexed) for storing conversation intent

This column is used to track the current intent (e.g., "race_plan", "season_plan")
during conversation progress tracking.
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


def migrate_add_conversation_progress_intent() -> None:
    """Add intent column to conversation_progress table if it doesn't exist."""
    logger.info("Starting intent column migration for conversation_progress table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if column already exists
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = 'conversation_progress'
                            AND column_name = 'intent'
                        )
                        """
                    )
                )
                intent_column_exists = result.scalar() is True
            else:
                result = conn.execute(text("PRAGMA table_info(conversation_progress)"))
                columns = [row[1] for row in result.fetchall()]
                intent_column_exists = "intent" in columns

            if intent_column_exists:
                logger.info(
                    "intent column already exists in conversation_progress table, skipping migration"
                )
                return

            logger.info("Adding intent column to conversation_progress table...")

            # Add intent column (nullable, String)
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE conversation_progress
                        ADD COLUMN intent VARCHAR NULL
                        """
                    )
                )
                # Create index on intent column
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_conversation_progress_intent
                        ON conversation_progress(intent)
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        """
                        ALTER TABLE conversation_progress
                        ADD COLUMN intent TEXT NULL
                        """
                    )
                )
                # SQLite creates index separately
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_conversation_progress_intent
                        ON conversation_progress(intent)
                        """
                    )
                )

            logger.info("Added intent column to conversation_progress table")

            logger.info("Conversation progress intent migration completed successfully")

        except Exception as e:
            logger.error(f"Error during conversation progress intent migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_conversation_progress_intent()
