"""Migration script to update daily_decisions table to match SQLAlchemy model.

This migration fixes the schema mismatch that causes /intelligence/today to fail.

Changes:
- Rename 'day' to 'decision_date' and change type to TIMESTAMPTZ
- Rename 'decision' to 'decision_data'
- Add metadata fields: recommendation_type, recommended_intensity, has_workout
- Add versioning fields: version, is_active
- Add relationship: weekly_intent_id
- Add updated_at timestamp
- Update constraints and indexes
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if using PostgreSQL database."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
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
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def migrate_daily_decisions_schema_v2() -> None:
    """Update daily_decisions table to match SQLAlchemy model."""
    print("Starting migration: daily_decisions schema v2")

    with engine.begin() as conn:
        if not _table_exists(conn, "daily_decisions"):
            print("Table daily_decisions does not exist. Creating it...")
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        CREATE TABLE daily_decisions (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            recommendation_type VARCHAR,
                            recommended_intensity VARCHAR,
                            has_workout BOOLEAN,
                            decision_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                            weekly_intent_id VARCHAR,
                            version INTEGER NOT NULL DEFAULT 1,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            decision_date TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                )
                # Create indexes
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_user_id ON daily_decisions(user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_decision_date ON daily_decisions(decision_date)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_weekly_intent_id ON daily_decisions(weekly_intent_id)"))
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_daily_decision_user_date_active
                        ON daily_decisions(user_id, decision_date)
                        WHERE is_active IS TRUE
                        """
                    )
                )
                # Create unique constraint
                conn.execute(
                    text(
                        """
                        ALTER TABLE daily_decisions
                        ADD CONSTRAINT uq_daily_decision_user_date_version
                        UNIQUE (user_id, decision_date, version)
                        """
                    )
                )
                print("✅ Created daily_decisions table with schema v2")
                return
            # SQLite
            conn.execute(
                text(
                    """
                        CREATE TABLE daily_decisions (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            recommendation_type VARCHAR,
                            recommended_intensity VARCHAR,
                            has_workout BOOLEAN,
                            decision_data TEXT NOT NULL DEFAULT '{}',
                            weekly_intent_id VARCHAR,
                            version INTEGER NOT NULL DEFAULT 1,
                            is_active BOOLEAN NOT NULL DEFAULT 1,
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            decision_date TIMESTAMP NOT NULL
                        )
                        """
                )
            )
            print("✅ Created daily_decisions table with schema v2 (SQLite)")
            return

        print("Table daily_decisions exists. Updating schema...")

        # Step 1: Add new columns if they don't exist
        columns_to_add = [
            ("recommendation_type", "VARCHAR", True),
            ("recommended_intensity", "VARCHAR", True),
            ("has_workout", "BOOLEAN", True),
            ("version", "INTEGER", False),
            ("is_active", "BOOLEAN", False),
            ("weekly_intent_id", "VARCHAR", True),
            ("updated_at", "TIMESTAMPTZ", False),
            ("decision_date", "TIMESTAMPTZ", True),
            ("decision_data", "JSONB", False),
        ]

        for column_name, column_type, nullable in columns_to_add:
            if not _column_exists(conn, "daily_decisions", column_name):
                print(f"Adding column: {column_name}")
                nullable_clause = "" if nullable else " NOT NULL"
                default_clause = ""

                if column_name == "version":
                    default_clause = " DEFAULT 1"
                elif column_name == "is_active":
                    default_clause = " DEFAULT TRUE"
                elif column_name in {"created_at", "updated_at"}:
                    default_clause = " DEFAULT now()"
                elif column_name == "decision_data":
                    default_clause = " DEFAULT '{}'::jsonb"

                if _is_postgresql():
                    conn.execute(
                        text(
                            f"ALTER TABLE daily_decisions ADD COLUMN {column_name} {column_type}{default_clause}{nullable_clause}"
                        )
                    )
                else:
                    # SQLite
                    conn.execute(
                        text(f"ALTER TABLE daily_decisions ADD COLUMN {column_name} {column_type}{default_clause}")
                    )
                print(f"✅ Added column: {column_name}")

        # Step 2: Migrate data from old columns to new columns
        if _column_exists(conn, "daily_decisions", "day") and _column_exists(conn, "daily_decisions", "decision_date"):
            print("Migrating data from 'day' to 'decision_date'...")
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        UPDATE daily_decisions
                        SET decision_date = (day AT TIME ZONE 'UTC')::timestamptz
                        WHERE decision_date IS NULL AND day IS NOT NULL
                        """
                    )
                )
            print("✅ Migrated 'day' to 'decision_date'")

        if _column_exists(conn, "daily_decisions", "decision") and _column_exists(conn, "daily_decisions", "decision_data"):
            print("Migrating data from 'decision' to 'decision_data'...")
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        UPDATE daily_decisions
                        SET decision_data = decision
                        WHERE decision_data = '{}'::jsonb
                        AND decision IS NOT NULL
                        AND decision != '{}'::jsonb
                        """
                    )
                )
            print("✅ Migrated 'decision' to 'decision_data'")

        # Step 3: Update user_id type if it's UUID
        if _is_postgresql():
            result = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'daily_decisions'
                    AND column_name = 'user_id'
                    """
                )
            )
            row = result.fetchone()
            if row and row[0] == "uuid":
                print("Converting user_id from UUID to VARCHAR...")
                # First, find and drop all foreign key constraints on user_id
                print("Finding foreign key constraints on user_id...")
                fk_result = conn.execute(
                    text(
                        """
                        SELECT constraint_name
                        FROM information_schema.table_constraints
                        WHERE table_schema = 'public'
                        AND table_name = 'daily_decisions'
                        AND constraint_type = 'FOREIGN KEY'
                        AND constraint_name IN (
                            SELECT constraint_name
                            FROM information_schema.key_column_usage
                            WHERE table_schema = 'public'
                            AND table_name = 'daily_decisions'
                            AND column_name = 'user_id'
                        )
                        """
                    )
                )
                fk_rows = fk_result.fetchall()
                for fk_row in fk_rows:
                    constraint_name = fk_row[0]
                    print(f"Dropping foreign key constraint: {constraint_name}")
                    conn.execute(
                        text(f"ALTER TABLE daily_decisions DROP CONSTRAINT IF EXISTS {constraint_name}")
                    )
                    print(f"✅ Dropped foreign key constraint: {constraint_name}")

                # Also try the common constraint name
                conn.execute(
                    text("ALTER TABLE daily_decisions DROP CONSTRAINT IF EXISTS daily_decisions_user_id_fkey")
                )

                # Now change the type
                conn.execute(
                    text("ALTER TABLE daily_decisions ALTER COLUMN user_id TYPE VARCHAR USING user_id::text")
                )
                print("✅ Converted user_id to VARCHAR")

                # Note: We don't recreate the FK constraint because:
                # 1. The model doesn't define a ForeignKey relationship for user_id
                # 2. users.id is VARCHAR, so if we wanted to add it back, we could, but it's not in the model

        # Step 4: Set NOT NULL constraints after data migration
        if _is_postgresql():
            if _column_exists(conn, "daily_decisions", "decision_date"):
                print("Setting NOT NULL constraint on decision_date...")
                try:
                    conn.execute(text("ALTER TABLE daily_decisions ALTER COLUMN decision_date SET NOT NULL"))
                    print("✅ Set NOT NULL on decision_date")
                except Exception as e:
                    print(f"⚠️  Could not set NOT NULL on decision_date (may have NULL values): {e}")

            if _column_exists(conn, "daily_decisions", "decision_data"):
                print("Setting NOT NULL constraint on decision_data...")
                try:
                    conn.execute(text("ALTER TABLE daily_decisions ALTER COLUMN decision_data SET NOT NULL"))
                    print("✅ Set NOT NULL on decision_data")
                except Exception as e:
                    print(f"⚠️  Could not set NOT NULL on decision_data (may have NULL values): {e}")

        # Step 5: Drop old columns if they exist
        if _column_exists(conn, "daily_decisions", "day"):
            print("Dropping old column: day")
            conn.execute(text("ALTER TABLE daily_decisions DROP COLUMN IF EXISTS day"))
            print("✅ Dropped column: day")

        if _column_exists(conn, "daily_decisions", "decision"):
            print("Dropping old column: decision")
            conn.execute(text("ALTER TABLE daily_decisions DROP COLUMN IF EXISTS decision"))
            print("✅ Dropped column: decision")

        # Step 6: Update constraints
        print("Updating constraints...")
        if _is_postgresql():
            # Drop old unique constraint if it exists
            conn.execute(
                text("ALTER TABLE daily_decisions DROP CONSTRAINT IF EXISTS daily_decisions_user_id_day_key")
            )

            # Create new unique constraint
            conn.execute(
                text("ALTER TABLE daily_decisions DROP CONSTRAINT IF EXISTS uq_daily_decision_user_date_version")
            )
            conn.execute(
                text(
                    """
                    ALTER TABLE daily_decisions
                    ADD CONSTRAINT uq_daily_decision_user_date_version
                    UNIQUE (user_id, decision_date, version)
                    """
                )
            )
            print("✅ Updated unique constraint")

        # Step 7: Create indexes
        print("Creating indexes...")
        if _is_postgresql():
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_user_id ON daily_decisions(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_decision_date ON daily_decisions(decision_date)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_decision_weekly_intent_id ON daily_decisions(weekly_intent_id)"))
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_daily_decision_user_date_active
                    ON daily_decisions(user_id, decision_date)
                    WHERE is_active IS TRUE
                    """
                )
            )
            print("✅ Created indexes")

        print("\n✅ Migration complete: daily_decisions table updated to schema v2")


if __name__ == "__main__":
    migrate_daily_decisions_schema_v2()
