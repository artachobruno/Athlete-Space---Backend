"""Migration script to add LLM interpretation fields to compliance tables.

This migration adds:
- step_compliance: LLM interpretation fields
  - llm_rating: Rating (VARCHAR, nullable)
  - llm_summary: Summary text (TEXT, nullable)
  - llm_tip: Coaching tip (TEXT, nullable)
  - llm_confidence: Confidence score (FLOAT, nullable)

- workout_compliance_summary: LLM interpretation fields
  - llm_summary: Summary text (TEXT, nullable)
  - llm_verdict: Verdict (VARCHAR, nullable)
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


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
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
    # SQLite
    result = conn.execute(
        text("PRAGMA table_info(:table_name)"),
        {"table_name": table_name},
    )
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def migrate_add_llm_interpretation_fields() -> None:
    """Add LLM interpretation fields to compliance tables if they don't exist."""
    logger.info("Starting LLM interpretation fields migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Add fields to step_compliance table
            logger.info("Checking step_compliance table...")

            if _is_postgresql():
                if not _column_exists(conn, "step_compliance", "llm_rating"):
                    logger.info("Adding llm_rating column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_rating VARCHAR")
                    )
                    logger.info("llm_rating column added")

                if not _column_exists(conn, "step_compliance", "llm_summary"):
                    logger.info("Adding llm_summary column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_summary TEXT")
                    )
                    logger.info("llm_summary column added")

                if not _column_exists(conn, "step_compliance", "llm_tip"):
                    logger.info("Adding llm_tip column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_tip TEXT")
                    )
                    logger.info("llm_tip column added")

                if not _column_exists(conn, "step_compliance", "llm_confidence"):
                    logger.info("Adding llm_confidence column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_confidence FLOAT")
                    )
                    logger.info("llm_confidence column added")
            else:
                # SQLite
                if not _column_exists(conn, "step_compliance", "llm_rating"):
                    logger.info("Adding llm_rating column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_rating TEXT")
                    )
                    logger.info("llm_rating column added")

                if not _column_exists(conn, "step_compliance", "llm_summary"):
                    logger.info("Adding llm_summary column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_summary TEXT")
                    )
                    logger.info("llm_summary column added")

                if not _column_exists(conn, "step_compliance", "llm_tip"):
                    logger.info("Adding llm_tip column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_tip TEXT")
                    )
                    logger.info("llm_tip column added")

                if not _column_exists(conn, "step_compliance", "llm_confidence"):
                    logger.info("Adding llm_confidence column to step_compliance...")
                    conn.execute(
                        text("ALTER TABLE step_compliance ADD COLUMN llm_confidence REAL")
                    )
                    logger.info("llm_confidence column added")

            # Add fields to workout_compliance_summary table
            logger.info("Checking workout_compliance_summary table...")

            if _is_postgresql():
                if not _column_exists(conn, "workout_compliance_summary", "llm_summary"):
                    logger.info("Adding llm_summary column to workout_compliance_summary...")
                    conn.execute(
                        text("ALTER TABLE workout_compliance_summary ADD COLUMN llm_summary TEXT")
                    )
                    logger.info("llm_summary column added")

                if not _column_exists(conn, "workout_compliance_summary", "llm_verdict"):
                    logger.info("Adding llm_verdict column to workout_compliance_summary...")
                    conn.execute(
                        text("ALTER TABLE workout_compliance_summary ADD COLUMN llm_verdict VARCHAR")
                    )
                    logger.info("llm_verdict column added")
            else:
                # SQLite
                if not _column_exists(conn, "workout_compliance_summary", "llm_summary"):
                    logger.info("Adding llm_summary column to workout_compliance_summary...")
                    conn.execute(
                        text("ALTER TABLE workout_compliance_summary ADD COLUMN llm_summary TEXT")
                    )
                    logger.info("llm_summary column added")

                if not _column_exists(conn, "workout_compliance_summary", "llm_verdict"):
                    logger.info("Adding llm_verdict column to workout_compliance_summary...")
                    conn.execute(
                        text("ALTER TABLE workout_compliance_summary ADD COLUMN llm_verdict TEXT")
                    )
                    logger.info("llm_verdict column added")

            logger.info("LLM interpretation fields migration completed successfully")

        except Exception as e:
            logger.error(f"Error during LLM interpretation fields migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_llm_interpretation_fields()
