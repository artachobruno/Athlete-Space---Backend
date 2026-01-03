"""Migration script to ensure activities table has all required columns.

This migration adds any missing columns to the activities table to match
the current Activity model schema.
"""

from sqlalchemy import text

from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _table_exists(conn) -> bool:
    """Check if activities table exists."""
    if _is_postgresql():
        result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'activities'"))
        return result.fetchone() is not None
    result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='activities'"))
    return result.fetchone() is not None


def _get_existing_columns(conn) -> set[str]:
    """Get set of existing column names in activities table."""
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = 'activities'
            """)
        )
        return {row[0] for row in result.fetchall()}
    result = conn.execute(text("PRAGMA table_info(activities)"))
    return {row[1] for row in result.fetchall()}


def _index_exists(conn, pg_index_name: str, sqlite_index_name: str) -> bool:
    """Check if an index exists on activities table."""
    index_name = pg_index_name if _is_postgresql() else sqlite_index_name
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM pg_indexes
                    WHERE schemaname = 'public'
                    AND tablename = 'activities'
                    AND indexname = :index_name
                )
            """),
            {"index_name": index_name},
        )
        return result.scalar() is True
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


def _create_index_if_not_exists(conn, column_name: str) -> None:
    """Create index on column if it doesn't exist."""
    pg_index_name = f"idx_activities_{column_name}"
    sqlite_index_name = f"ix_activities_{column_name}"

    if not _index_exists(conn, pg_index_name, sqlite_index_name):
        print(f"Creating index on {column_name}...")
        if _is_postgresql():
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {pg_index_name} ON activities ({column_name})"))
        else:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {sqlite_index_name} ON activities ({column_name})"))


def _add_missing_columns(conn, existing_columns: set[str]) -> set[str]:
    """Add missing columns to activities table. Returns updated column set."""
    required_columns = {
        "user_id": ("VARCHAR", "TEXT"),
        "strava_activity_id": ("VARCHAR", "TEXT"),
        "start_time": ("TIMESTAMP", "DATETIME"),
        "type": ("VARCHAR", "TEXT"),
        "duration_seconds": ("INTEGER", "INTEGER"),
        "distance_meters": ("REAL", "REAL"),
        "elevation_gain_meters": ("REAL", "REAL"),
        "raw_json": ("JSONB", "JSON"),
        "created_at": ("TIMESTAMP", "DATETIME"),
    }

    for column_name, (pg_type, sqlite_type) in required_columns.items():
        if column_name not in existing_columns:
            column_type = pg_type if _is_postgresql() else sqlite_type
            print(f"Adding missing column: {column_name} ({column_type})")
            alter_sql = f"ALTER TABLE activities ADD COLUMN {column_name} {column_type}"
            conn.execute(text(alter_sql))
            print(f"Added column: {column_name}")

    return existing_columns | set(required_columns.keys())


def migrate_activities_schema() -> None:
    """Add missing columns to activities table to match Activity model."""
    with engine.begin() as conn:
        if not _table_exists(conn):
            print("activities table does not exist. Skipping migration (table will be created by Base.metadata.create_all).")
            return

        existing_columns = _get_existing_columns(conn)
        print(f"Existing columns: {sorted(existing_columns)}")

        updated_columns = _add_missing_columns(conn, existing_columns)

        if "user_id" in updated_columns:
            _create_index_if_not_exists(conn, "user_id")

        if "start_time" in updated_columns:
            _create_index_if_not_exists(conn, "start_time")

        print("Migration complete: Activities table schema updated.")


if __name__ == "__main__":
    migrate_activities_schema()
