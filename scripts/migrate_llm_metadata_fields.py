"""Migration script to add metadata fields to LLM tables and composite indexes.

This migration adds:
- Metadata fields to season_plans, weekly_intents, daily_decisions, weekly_reports
- Composite indexes for common query patterns

Architecture: Metadata fields enable fast queries without JSON parsing.
Full payload remains in JSON fields for detailed access when needed.
"""

from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if using PostgreSQL database."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Note: table_name and column_name are trusted (hardcoded in migration scripts).
    """
    if _is_postgresql():
        # Use parameterized query for safety
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
    # SQLite - PRAGMA doesn't support parameters, but table_name is trusted
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def _index_exists(conn, index_name: str) -> bool:
    """Check if an index exists.

    Note: index_name is trusted (hardcoded in migration scripts).
    """
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                AND indexname = :index_name
                """
            ),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


def _add_column_if_not_exists(conn, table_name: str, column_name: str, column_type: str, nullable: bool = True) -> None:
    """Add a column to a table if it doesn't exist.

    Note: table_name, column_name, column_type are trusted (hardcoded in migration scripts).
    """
    if _column_exists(conn, table_name, column_name):
        print(f"Column {table_name}.{column_name} already exists, skipping.")
        return

    print(f"Adding column {table_name}.{column_name}...")
    nullable_clause = "" if nullable else " NOT NULL"
    # Table/column names are trusted in migration scripts
    if _is_postgresql():
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{nullable_clause}"))
    else:
        # SQLite doesn't support NOT NULL on ALTER TABLE without default
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
    print(f"Added column {table_name}.{column_name}.")


def _create_index_if_not_exists(conn, index_name: str, table_name: str, columns: str) -> None:
    """Create an index if it doesn't exist.

    Note: index_name, table_name, columns are trusted (hardcoded in migration scripts).
    """
    if _index_exists(conn, index_name):
        print(f"Index {index_name} already exists, skipping.")
        return

    print(f"Creating index {index_name} on {table_name}({columns})...")
    # Index/table/column names are trusted in migration scripts
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})"))
    print(f"Created index {index_name}.")


def migrate_llm_metadata_fields() -> None:
    """Add metadata fields to LLM tables and composite indexes."""
    with engine.begin() as conn:
        # ===== SeasonPlan metadata fields =====
        print("\n=== Migrating season_plans table ===")
        _add_column_if_not_exists(conn, "season_plans", "plan_name", "VARCHAR", nullable=True)
        _add_column_if_not_exists(conn, "season_plans", "start_date", "TIMESTAMP", nullable=True)
        _add_column_if_not_exists(conn, "season_plans", "end_date", "TIMESTAMP", nullable=True)
        _add_column_if_not_exists(conn, "season_plans", "primary_race_date", "TIMESTAMP", nullable=True)
        _add_column_if_not_exists(conn, "season_plans", "primary_race_name", "VARCHAR", nullable=True)
        _add_column_if_not_exists(conn, "season_plans", "total_weeks", "INTEGER", nullable=True)

        # Create index on plan_name for filtering
        _create_index_if_not_exists(conn, "idx_season_plans_plan_name", "season_plans", "plan_name")

        # ===== WeeklyIntent metadata fields =====
        print("\n=== Migrating weekly_intents table ===")
        _add_column_if_not_exists(conn, "weekly_intents", "primary_focus", "VARCHAR", nullable=True)
        _add_column_if_not_exists(conn, "weekly_intents", "total_sessions", "INTEGER", nullable=True)
        _add_column_if_not_exists(conn, "weekly_intents", "target_volume_hours", "REAL", nullable=True)

        # ===== DailyDecision metadata fields =====
        print("\n=== Migrating daily_decisions table ===")
        _add_column_if_not_exists(conn, "daily_decisions", "recommendation_type", "VARCHAR", nullable=True)
        _add_column_if_not_exists(conn, "daily_decisions", "recommended_intensity", "VARCHAR", nullable=True)
        _add_column_if_not_exists(conn, "daily_decisions", "has_workout", "BOOLEAN", nullable=True)

        # ===== WeeklyReport metadata fields =====
        print("\n=== Migrating weekly_reports table ===")
        _add_column_if_not_exists(conn, "weekly_reports", "summary_score", "REAL", nullable=True)
        _add_column_if_not_exists(conn, "weekly_reports", "key_insights_count", "INTEGER", nullable=True)
        _add_column_if_not_exists(conn, "weekly_reports", "activities_completed", "INTEGER", nullable=True)
        _add_column_if_not_exists(conn, "weekly_reports", "adherence_percentage", "REAL", nullable=True)

        # ===== Composite indexes for common query patterns =====
        print("\n=== Creating composite indexes ===")
        _create_index_if_not_exists(conn, "idx_activities_user_start_time", "activities", "user_id, start_time")
        _create_index_if_not_exists(conn, "idx_planned_sessions_user_date", "planned_sessions", "user_id, date")

        # Note: daily_training_load and weekly_training_summary already have unique constraints
        # that serve as indexes, but we make them explicit for clarity
        if not _index_exists(conn, "idx_daily_load_user_date"):
            print("Note: daily_training_load already has unique constraint covering (user_id, date)")
        if not _index_exists(conn, "idx_weekly_summary_user_week"):
            print("Note: weekly_training_summary already has unique constraint covering (user_id, week_start)")

        print("\n✅ Migration complete: Added LLM metadata fields and composite indexes.")


if __name__ == "__main__":
    migrate_llm_metadata_fields()
