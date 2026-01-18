"""Migration script to add duration_diff_pct column to pairing_decisions table.

This column was added to the PairingDecision model but the database schema
hasn't been updated yet. This migration adds the column to track duration
difference percentage for pairing decisions.

Usage:
    python scripts/migrate_add_duration_diff_pct_to_pairing_decisions.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db.session import get_session


def migrate():
    """Add duration_diff_pct column to pairing_decisions table."""
    logger.info("Starting migration: add duration_diff_pct to pairing_decisions")

    with get_session() as session:
        try:
            # Check if column already exists
            check_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'pairing_decisions'
                AND column_name = 'duration_diff_pct'
            """)
            result = session.execute(check_query).first()

            if result:
                logger.info("Column duration_diff_pct already exists in pairing_decisions table")
                return

            # Add the column
            alter_query = text("""
                ALTER TABLE pairing_decisions
                ADD COLUMN duration_diff_pct FLOAT NULL
            """)

            session.execute(alter_query)
            session.commit()

            logger.info("✅ Successfully added duration_diff_pct column to pairing_decisions table")

        except ProgrammingError as e:
            session.rollback()
            logger.error(f"❌ Migration failed: {e}")
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Unexpected error during migration: {e}")
            raise


if __name__ == "__main__":
    migrate()
