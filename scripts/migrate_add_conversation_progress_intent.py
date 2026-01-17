"""Migration script to add missing columns to conversation_progress table.

This migration adds:
- intent: String column (nullable, indexed) for storing conversation intent
- slots: JSON/JSONB column (NOT NULL, default '{}') for slot values
- awaiting_slots: JSON/JSONB column (NOT NULL, default '[]') for awaited slot names

These columns are required for conversation progress tracking and slot extraction.
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
    """Add missing columns (intent, slots, awaiting_slots) to conversation_progress table if they don't exist."""
    logger.info("Starting conversation_progress columns migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check which columns already exist
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'conversation_progress'
                        """
                    )
                )
                existing_columns = {row[0] for row in result.fetchall()}
            else:
                result = conn.execute(text("PRAGMA table_info(conversation_progress)"))
                existing_columns = {row[1] for row in result.fetchall()}

            intent_exists = "intent" in existing_columns
            slots_exists = "slots" in existing_columns
            awaiting_slots_exists = "awaiting_slots" in existing_columns

            if intent_exists and slots_exists and awaiting_slots_exists:
                logger.info(
                    "All required columns (intent, slots, awaiting_slots) already exist in conversation_progress table, skipping migration"
                )
                return

            logger.info("Adding missing columns to conversation_progress table...")

            if _is_postgresql():
                # Add intent column if missing
                if not intent_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN intent VARCHAR NULL
                            """
                        )
                    )
                    logger.info("Added intent column")
                    # Create index on intent column
                    conn.execute(
                        text(
                            """
                            CREATE INDEX IF NOT EXISTS idx_conversation_progress_intent
                            ON conversation_progress(intent)
                            """
                        )
                    )

                # Add slots column if missing
                if not slots_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN slots JSONB NOT NULL DEFAULT '{}'::jsonb
                            """
                        )
                    )
                    logger.info("Added slots column")

                # Add awaiting_slots column if missing
                if not awaiting_slots_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN awaiting_slots JSONB NOT NULL DEFAULT '[]'::jsonb
                            """
                        )
                    )
                    logger.info("Added awaiting_slots column")
            else:
                # SQLite
                # Add intent column if missing
                if not intent_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN intent TEXT NULL
                            """
                        )
                    )
                    logger.info("Added intent column")
                    conn.execute(
                        text(
                            """
                            CREATE INDEX IF NOT EXISTS idx_conversation_progress_intent
                            ON conversation_progress(intent)
                            """
                        )
                    )

                # Add slots column if missing
                if not slots_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN slots TEXT NOT NULL DEFAULT '{}'
                            """
                        )
                    )
                    logger.info("Added slots column")

                # Add awaiting_slots column if missing
                if not awaiting_slots_exists:
                    conn.execute(
                        text(
                            """
                            ALTER TABLE conversation_progress
                            ADD COLUMN awaiting_slots TEXT NOT NULL DEFAULT '[]'
                            """
                        )
                    )
                    logger.info("Added awaiting_slots column")

            logger.info("Conversation progress columns migration completed successfully")

        except Exception as e:
            logger.error(f"Error during conversation progress columns migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_conversation_progress_intent()
