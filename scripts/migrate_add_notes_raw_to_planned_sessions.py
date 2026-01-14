"""Migration script to add notes_raw field to planned_sessions table.

This migration adds:
- notes_raw field to preserve immutable user input
- notes field remains for backward compatibility (optional derived notes)
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
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def _add_column_if_not_exists(conn, table_name: str, column_name: str, column_type: str, nullable: bool = True) -> None:
    """Add a column to a table if it doesn't exist.

    Note: table_name, column_name, column_type are trusted (hardcoded in migration scripts).
    """
    if _column_exists(conn, table_name, column_name):
        print(f"Column {table_name}.{column_name} already exists, skipping.")
        return

    print(f"Adding column {table_name}.{column_name}...")
    nullable_clause = "" if nullable else " NOT NULL"
    if _is_postgresql():
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{nullable_clause}"))
    else:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
    print(f"Added column {table_name}.{column_name}.")


def migrate_add_notes_raw_to_planned_sessions() -> None:
    """Add notes_raw field to planned_sessions table."""
    with engine.begin() as conn:
        print("\n=== Migrating planned_sessions table ===")
        _add_column_if_not_exists(conn, "planned_sessions", "notes_raw", "TEXT", nullable=True)
        print("\n✅ Migration complete: Added notes_raw field to planned_sessions.")


if __name__ == "__main__":
    migrate_add_notes_raw_to_planned_sessions()
