"""Migration script to change activities.id column from integer to UUID string.

This migration:
1. Checks if the id column is currently an integer
2. Converts it to VARCHAR/TEXT to support UUID strings
3. Generates UUIDs for existing records if needed
"""

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


def migrate_activities_id_to_uuid() -> None:
    """Migrate activities.id column from integer to UUID string.
    
    NOTE: This migration should be run in PRODUCTION where the actual database is.
    If running locally against SQLite, the table may not exist yet or may already
    have the correct schema.
    """
    is_postgres = _is_postgresql()
    db_type = "PostgreSQL" if is_postgres else "SQLite"
    print(f"Detected database type: {db_type}")
    print(f"Database URL: {engine.url}")
    
    if not is_postgres:
        print("\n⚠️  WARNING: You are running this migration against SQLite (local development).")
        print("The production error is from PostgreSQL on Render.")
        print("This migration should be run in production where the actual database is.")
        print("\nTo run in production:")
        print("1. SSH into your Render service or use Render's shell")
        print("2. Run: python scripts/migrate_activities_id_to_uuid.py")
        print("\nContinuing with local migration check...\n")
    
    with engine.begin() as conn:
        # Check if table exists
        if _is_postgresql():
            result = conn.execute(
                text("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'activities')")
            )
            table_exists = result.scalar()
        else:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='activities'"))
            table_exists = result.fetchone() is not None

        if not table_exists:
            print("activities table does not exist. Skipping migration.")
            return

        # Check current column type
        current_type = _get_column_type(conn, "id")
        print(f"Current id column type: {current_type}")

        if current_type is None:
            print("id column not found.")
            if not _table_has_data(conn):
                print("Table exists but has no id column and no data.")
                print("This is likely a new table that will be created with the correct schema.")
                print("No migration needed - the table will be created with UUID id when first used.")
            else:
                print("WARNING: Table has data but no id column found. This is unexpected.")
                print("Please check the table schema manually.")
            return

        # Check if already migrated
        if _is_postgresql():
            is_string_type = current_type in ("character varying", "varchar", "text", "uuid")
        else:
            is_string_type = current_type.upper() in ("TEXT", "VARCHAR")

        if is_string_type:
            print("id column is already a string type. No migration needed.")
            return

        print("Migrating id column from integer to string (UUID)...")

        if _is_postgresql():
            # PostgreSQL migration
            has_data = _table_has_data(conn)

            if has_data:
                print("Table has existing data. This migration requires manual intervention.")
                print("Please backup your database before proceeding.")
                print("\nTo complete the migration manually:")
                print("1. Generate UUIDs for existing records")
                print("2. Alter the column type to VARCHAR")
                print("3. Update existing records with UUIDs")
                print("\nExample SQL:")
                print("""
-- Step 1: Add temporary UUID column
ALTER TABLE activities ADD COLUMN id_new VARCHAR;

-- Step 2: Generate UUIDs for existing records
UPDATE activities SET id_new = gen_random_uuid()::text;

-- Step 3: Drop old column and rename new one
ALTER TABLE activities DROP CONSTRAINT activities_pkey;
ALTER TABLE activities DROP COLUMN id;
ALTER TABLE activities RENAME COLUMN id_new TO id;
ALTER TABLE activities ALTER COLUMN id SET NOT NULL;
ALTER TABLE activities ADD PRIMARY KEY (id);
CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id);
                """)
                return

            # No data, safe to alter directly
            print("No existing data. Altering column type directly...")
            conn.execute(text("ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_pkey"))
            conn.execute(text("ALTER TABLE activities ALTER COLUMN id TYPE VARCHAR USING id::text"))
            conn.execute(text("ALTER TABLE activities ADD PRIMARY KEY (id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id)"))
            print("Migration complete: id column is now VARCHAR (UUID).")
        else:
            # SQLite migration
            has_data = _table_has_data(conn)

            if has_data:
                print("Table has existing data. SQLite requires table recreation.")
                print("Please backup your database before proceeding.")
                print("\nSQLite migration steps:")
                print("1. Create new table with TEXT id")
                print("2. Copy data with generated UUIDs")
                print("3. Drop old table and rename new one")
                return

            # No data, safe to recreate table
            print("No existing data. Recreating table with TEXT id...")
            conn.execute(text("""
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
            """))
            conn.execute(text("DROP TABLE activities"))
            conn.execute(text("ALTER TABLE activities_new RENAME TO activities"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_id ON activities (id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_user_id ON activities (user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities (start_time)"))
            print("Migration complete: id column is now TEXT (UUID).")


if __name__ == "__main__":
    migrate_activities_id_to_uuid()

