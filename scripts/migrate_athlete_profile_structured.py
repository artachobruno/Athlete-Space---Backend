"""Migration script to add structured profile columns and athlete_bios table.

This migration adds:
- JSONB columns to athlete_profiles table:
  - identity: JSONB identity information
  - goals: JSONB goals information
  - constraints: JSONB constraints information
  - training_context: JSONB training context information
  - preferences: JSONB preferences information

- athlete_bios table:
  - id: UUID primary key
  - user_id: Foreign key to users.id (indexed)
  - text: Bio text (TEXT)
  - confidence_score: Confidence score (FLOAT)
  - source: Bio source ('ai_generated', 'user_edited', 'manual')
  - depends_on_hash: Hash of profile data this bio depends on (VARCHAR, nullable)
  - last_generated_at: Last generation timestamp (TIMESTAMP, nullable)
  - stale: Whether bio is stale (BOOLEAN)
  - created_at: Creation timestamp
  - updated_at: Last update timestamp

Constraints:
- Unique index on athlete_profiles.user_id (already exists)
- Index on athlete_bios.user_id for fast lookups
- Index on athlete_bios.stale for fast stale bio queries
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
                WHERE table_name = :table_name
                AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists."""
    if _is_postgresql():
        result = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def migrate_athlete_profile_structured() -> None:
    """Add structured profile columns and create athlete_bios table."""
    logger.info("Starting athlete profile structured migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Add JSONB columns to athlete_profiles table
            jsonb_columns = ["identity", "goals", "constraints", "training_context", "preferences"]

            for column_name in jsonb_columns:
                if _column_exists(conn, "athlete_profiles", column_name):
                    logger.info(f"Column athlete_profiles.{column_name} already exists, skipping")
                else:
                    logger.info(f"Adding column athlete_profiles.{column_name}...")

                    if _is_postgresql():
                        conn.execute(
                            text(
                                f"""
                                ALTER TABLE athlete_profiles
                                ADD COLUMN {column_name} JSONB
                                """
                            )
                        )
                    else:
                        # SQLite doesn't have JSONB, use TEXT
                        conn.execute(
                            text(
                                f"""
                                ALTER TABLE athlete_profiles
                                ADD COLUMN {column_name} TEXT
                                """
                            )
                        )

                    logger.info(f"Column athlete_profiles.{column_name} added successfully")

            # Create athlete_bios table
            if _table_exists(conn, "athlete_bios"):
                logger.info("athlete_bios table already exists, skipping")
            else:
                logger.info("Creating athlete_bios table...")

                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            CREATE TABLE athlete_bios (
                                id VARCHAR PRIMARY KEY,
                                user_id VARCHAR NOT NULL,
                                text TEXT NOT NULL,
                                confidence_score FLOAT NOT NULL DEFAULT 0.0,
                                source VARCHAR NOT NULL,
                                depends_on_hash VARCHAR,
                                last_generated_at TIMESTAMP WITH TIME ZONE,
                                stale BOOLEAN NOT NULL DEFAULT FALSE,
                                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                                CONSTRAINT fk_athlete_bios_user_id
                                    FOREIGN KEY (user_id) REFERENCES users(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_athlete_bios_user_id
                            ON athlete_bios (user_id)
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_athlete_bios_stale
                            ON athlete_bios (stale)
                            """
                        )
                    )
                else:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE athlete_bios (
                                id TEXT PRIMARY KEY,
                                user_id TEXT NOT NULL,
                                text TEXT NOT NULL,
                                confidence_score REAL NOT NULL DEFAULT 0.0,
                                source TEXT NOT NULL,
                                depends_on_hash TEXT,
                                last_generated_at DATETIME,
                                stale INTEGER NOT NULL DEFAULT 0,
                                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_athlete_bios_user_id
                            ON athlete_bios (user_id)
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_athlete_bios_stale
                            ON athlete_bios (stale)
                            """
                        )
                    )

                logger.info("athlete_bios table created successfully")

            logger.info("Athlete profile structured migration completed successfully")

        except Exception as e:
            logger.error(f"Error during athlete profile structured migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_athlete_profile_structured()
