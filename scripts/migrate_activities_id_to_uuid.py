"""Migration script to change activities.id column from integer to UUID string.

This migration:
1. Checks if the id column is currently an integer
2. Converts it to VARCHAR/TEXT to support UUID strings
3. Generates UUIDs for existing records if needed
"""

from loguru import logger
from sqlalchemy import text

from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _get_column_type(conn, column_name: str) -> str | None:
    """Get the data type of a column."""
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = 'activities'
                AND column_name = :column_name
            """),
            {"column_name": column_name},
        )
        row = result.fetchone()
        return row[0] if row else None
    # SQLite
    result = conn.execute(text("PRAGMA table_info(activities)"))
    rows = result.fetchall()
    for row in rows:
        # SQLite PRAGMA table_info returns: (cid, name, type, notnull, default_value, pk)
        if len(row) >= 3 and row[1] == column_name:  # column name is at index 1
            return row[2].upper() if row[2] else None  # type is at index 2
    return None


def _table_has_data(conn) -> bool:
    """Check if activities table has any data."""
    result = conn.execute(text("SELECT COUNT(*) FROM activities"))
    count = result.scalar()
    return count > 0 if count is not None else False


def _check_table_exists(conn) -> bool:
    """Check if activities table exists."""
    if _is_postgresql():
        result = conn.execute(text("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'activities')"))
        return bool(result.scalar())
    result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='activities'"))
    return result.fetchone() is not None


def _is_string_type(current_type: str | None) -> bool:
    """Check if current type is a string type."""
    if current_type is None:
        return False
    if _is_postgresql():
        return current_type in {"character varying", "varchar", "text", "uuid"}
    return current_type.upper() in {"TEXT", "VARCHAR"}


def _migrate_postgresql_with_data(conn) -> None:
    """Migrate PostgreSQL table with existing data."""
    logger.info("Table has existing data. Migrating with data preservation...")
    logger.info("Step 1: Adding temporary id_new column...")
    conn.execute(text("ALTER TABLE activities ADD COLUMN IF NOT EXISTS id_new VARCHAR"))
    logger.info("Step 2: Generating UUIDs for existing records...")
    conn.execute(text("UPDATE activities SET id_new = gen_random_uuid()::text WHERE id_new IS NULL"))
    logger.info("Step 3: Dropping old id column and renaming new one...")
    conn.execute(text("ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_pkey"))
    conn.execute(text("ALTER TABLE activities DROP COLUMN IF EXISTS id"))
    conn.execute(text("ALTER TABLE activities RENAME COLUMN id_new TO id"))
    conn.execute(text("ALTER TABLE activities ALTER COLUMN id SET NOT NULL"))
    conn.execute(text("ALTER TABLE activities ADD PRIMARY KEY (id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id)"))
    logger.info("Migration complete: id column is now VARCHAR (UUID) with existing data preserved.")


def _migrate_postgresql_no_data(conn) -> None:
    """Migrate PostgreSQL table without data."""
    logger.info("No existing data. Altering column type directly...")
    conn.execute(text("ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_pkey"))
    conn.execute(text("ALTER TABLE activities ALTER COLUMN id TYPE VARCHAR USING id::text"))
    conn.execute(text("ALTER TABLE activities ADD PRIMARY KEY (id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id)"))
    logger.info("Migration complete: id column is now VARCHAR (UUID).")


def _migrate_sqlite_no_data(conn) -> None:
    """Migrate SQLite table without data."""
    print("No existing data. Recreating table with TEXT id...")
    conn.execute(
        text("""
        CREATE TABLE activities_new (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            strava_activity_id TEXT NOT NULL,
            start_time DATETIME NOT NULL,
            type TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            distance_meters REAL NOT NULL,
            elevation_gain_meters REAL NOT NULL,
            raw_json JSON,
            created_at DATETIME NOT NULL,
            UNIQUE(user_id, strava_activity_id)
        )
    """)
    )
    conn.execute(text("DROP TABLE activities"))
    conn.execute(text("ALTER TABLE activities_new RENAME TO activities"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_user_id ON activities (user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities (start_time)"))
    print("Migration complete: id column is now TEXT (UUID).")


def migrate_activities_id_to_uuid() -> None:
    """Migrate activities.id column from integer to UUID string.

    NOTE: This migration should be run in PRODUCTION where the actual database is.
    If running locally against SQLite, the table may not exist yet or may already
    have the correct schema.
    """
    is_postgres = _is_postgresql()
    db_type = "PostgreSQL" if is_postgres else "SQLite"
    logger.info(f"Detected database type: {db_type}")
    logger.info(f"Database URL: {engine.url}")

    if not is_postgres:
        logger.warning("⚠️  WARNING: You are running this migration against SQLite (local development).")
        logger.warning("The production error is from PostgreSQL on Render.")
        logger.warning("This migration should be run in production where the actual database is.")
        logger.warning("Continuing with local migration check...")

    with engine.begin() as conn:
        if not _check_table_exists(conn):
            logger.info("activities table does not exist. Skipping migration (table will be created with correct schema).")
            return

        current_type = _get_column_type(conn, "id")
        logger.info(f"Current id column type: {current_type}")

        if current_type is None:
            if not _table_has_data(conn):
                logger.info("Table exists but has no id column and no data.")
                logger.info("This is likely a new table that will be created with the correct schema.")
                logger.info("No migration needed - the table will be created with UUID id when first used.")
            else:
                logger.warning("WARNING: Table has data but no id column found. This is unexpected.")
                logger.warning("Please check the table schema manually.")
            return

        if _is_string_type(current_type):
            logger.info("id column is already a string type. No migration needed.")
            return

        logger.info("Migrating id column from integer to string (UUID)...")

        if _is_postgresql():
            if _table_has_data(conn):
                _migrate_postgresql_with_data(conn)
            else:
                _migrate_postgresql_no_data(conn)
        else:
            if _table_has_data(conn):
                print("Table has existing data. SQLite requires table recreation.")
                print("Please backup your database before proceeding.")
                print("\nSQLite migration steps:")
                print("1. Create new table with TEXT id")
                print("2. Copy data with generated UUIDs")
                print("3. Drop old table and rename new one")
                return
            _migrate_sqlite_no_data(conn)


if __name__ == "__main__":
    migrate_activities_id_to_uuid()
