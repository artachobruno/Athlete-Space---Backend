"""Migration script to add user_id column to activities table.

This migration:
- Adds user_id column to activities table
- Maps existing activities from athlete_id (if exists) to user_id via StravaAccount
- Makes user_id NOT NULL after migration
"""

from sqlalchemy import text

from app.db.session import engine


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


def _check_columns_exist(conn) -> tuple[bool, bool]:
    """Check if user_id and athlete_id columns exist.

    Returns:
        Tuple of (user_id_exists, athlete_id_exists)
    """
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'activities'
                    AND column_name = 'user_id'
                )
            """)
        )
        user_id_exists = result.scalar() is True

        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'activities'
                    AND column_name = 'athlete_id'
                )
            """)
        )
        athlete_id_exists = result.scalar() is True
    else:
        result = conn.execute(text("PRAGMA table_info(activities)"))
        columns = {row[1]: row[2] for row in result.fetchall()}
        user_id_exists = "user_id" in columns
        athlete_id_exists = "athlete_id" in columns

    return user_id_exists, athlete_id_exists


def _add_user_id_column(conn) -> None:
    """Add user_id column to activities table."""
    if _is_postgresql():
        conn.execute(text("ALTER TABLE activities ADD COLUMN user_id VARCHAR"))
    else:
        conn.execute(text("ALTER TABLE activities ADD COLUMN user_id TEXT"))


def _create_user_id_index(conn) -> None:
    """Create index on user_id column."""
    if _is_postgresql():
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activities_user_id ON activities (user_id)"))


def _map_athlete_id_to_user_id(conn) -> None:
    """Map existing activities from athlete_id to user_id via StravaAccount."""
    if _is_postgresql():
        conn.execute(
            text("""
                UPDATE activities
                SET user_id = (
                    SELECT strava_accounts.user_id
                    FROM strava_accounts
                    WHERE strava_accounts.athlete_id = CAST(activities.athlete_id AS VARCHAR)
                    LIMIT 1
                )
                WHERE user_id IS NULL AND athlete_id IS NOT NULL
            """)
        )
    else:
        conn.execute(
            text("""
                UPDATE activities
                SET user_id = (
                    SELECT strava_accounts.user_id
                    FROM strava_accounts
                    WHERE strava_accounts.athlete_id = CAST(activities.athlete_id AS TEXT)
                    LIMIT 1
                )
                WHERE user_id IS NULL AND athlete_id IS NOT NULL
            """)
        )


def _set_user_id_from_first_account(conn) -> None:
    """Set user_id for activities without athlete_id using first StravaAccount."""
    conn.execute(
        text("""
            UPDATE activities
            SET user_id = (
                SELECT user_id FROM strava_accounts LIMIT 1
            )
            WHERE user_id IS NULL
        """)
    )


def _make_user_id_not_null(conn) -> None:
    """Make user_id NOT NULL if all rows have user_id."""
    if _is_postgresql():
        result = conn.execute(text("SELECT COUNT(*) FROM activities WHERE user_id IS NULL"))
        null_count = result.scalar()
        if null_count == 0:
            conn.execute(text("ALTER TABLE activities ALTER COLUMN user_id SET NOT NULL"))
        else:
            print(f"Warning: {null_count} activities still have NULL user_id, cannot set NOT NULL")
    else:
        print("Note: SQLite doesn't support ALTER COLUMN - user_id remains nullable")


def migrate_activities_user_id() -> None:
    """Add user_id column to activities table and migrate existing data."""
    with engine.begin() as conn:
        # Check if table exists first
        if not _table_exists(conn):
            print("activities table does not exist. Skipping migration (table will be created by Base.metadata.create_all).")
            return

        user_id_exists, athlete_id_exists = _check_columns_exist(conn)

        if user_id_exists:
            print("user_id column already exists in activities table, skipping migration")
            return

        print("Adding user_id column to activities table...")

        _add_user_id_column(conn)

        print("Creating index on user_id...")
        _create_user_id_index(conn)

        if athlete_id_exists:
            print("Mapping existing activities from athlete_id to user_id...")
            _map_athlete_id_to_user_id(conn)

        print("Setting user_id for activities without athlete_id...")
        _set_user_id_from_first_account(conn)

        print("Making user_id NOT NULL...")
        _make_user_id_not_null(conn)

        print("Migration complete: Added user_id column to activities table")


if __name__ == "__main__":
    migrate_activities_user_id()
