"""Migration script to create athlete_bios table.

This migration creates:
- athlete_bios: Stores generated narrative bio for athletes
  - id: UUID primary key (VARCHAR)
  - user_id: Foreign key to users.id (VARCHAR, indexed)
  - text: Bio content (TEXT, required)
  - confidence_score: Confidence score 0.0-1.0 (FLOAT, default: 0.0)
  - source: Bio source ('ai_generated', 'user_edited', 'manual') (VARCHAR, required)
  - depends_on_hash: Hash of profile data this bio depends on (VARCHAR, nullable)
  - last_generated_at: When bio was last generated (TIMESTAMP WITH TIME ZONE, nullable)
  - stale: Whether bio is stale (BOOLEAN, default: false)
  - created_at: Creation timestamp (TIMESTAMP WITH TIME ZONE, required)
  - updated_at: Last update timestamp (TIMESTAMP WITH TIME ZONE, required)

Constraints:
- Foreign key: athlete_bios.user_id -> users.id
- Index on user_id for fast user queries
- Index on stale for filtering stale bios
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


def _resolve_users_id_type_bios(conn) -> str:
    """Resolve users.id type for PostgreSQL (UUID or VARCHAR)."""
    if not _is_postgresql():
        return "VARCHAR"
    r = conn.execute(
        text(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'id'
            """
        )
    ).fetchone()
    if not r:
        return "VARCHAR"
    dt = r[0]
    if dt == "uuid":
        return "UUID"
    if dt == "character varying":
        return "VARCHAR"
    return "VARCHAR"


def _create_athlete_bios_postgres(conn) -> None:
    """Create athlete_bios table on PostgreSQL."""
    user_id_type = _resolve_users_id_type_bios(conn)
    id_type = "VARCHAR"
    conn.execute(
        text(
            f"""
            CREATE TABLE athlete_bios (
                id {id_type} PRIMARY KEY,
                user_id {user_id_type} NOT NULL,
                text TEXT NOT NULL,
                confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                source VARCHAR NOT NULL,
                depends_on_hash VARCHAR,
                last_generated_at TIMESTAMP WITH TIME ZONE,
                stale BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_athlete_bios_user_id FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX idx_athlete_bios_user_id ON athlete_bios (user_id)"))
    conn.execute(text("CREATE INDEX idx_athlete_bios_stale ON athlete_bios (stale)"))


def _create_athlete_bios_sqlite(conn) -> None:
    """Create athlete_bios table on SQLite."""
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
    conn.execute(text("CREATE INDEX idx_athlete_bios_user_id ON athlete_bios (user_id)"))
    conn.execute(text("CREATE INDEX idx_athlete_bios_stale ON athlete_bios (stale)"))


def migrate_create_athlete_bios_table() -> None:
    """Create athlete_bios table if it doesn't exist."""
    logger.info("Starting athlete_bios table migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            if _table_exists(conn, "athlete_bios"):
                logger.info("athlete_bios table already exists, skipping migration")
                return

            logger.info("Creating athlete_bios table...")
            if _is_postgresql():
                _create_athlete_bios_postgres(conn)
            else:
                _create_athlete_bios_sqlite(conn)
            logger.info("athlete_bios table created successfully")

        except Exception as e:
            logger.error(f"Error during athlete_bios table migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_create_athlete_bios_table()
