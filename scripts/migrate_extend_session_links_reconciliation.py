"""Migration: Extend session_links table with reconciliation fields.

PHASE 3.1: Elevate session_links into a reconciliation object.

This migration adds:
- match_reason: JSONB with pairing rationale
- deltas: JSONB with planned vs actual differences
- resolved_at: Timestamp when reconciliation was confirmed
"""

import sys
from pathlib import Path

# Add project root to Python path
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import get_session


def migrate_extend_session_links_reconciliation() -> None:
    """Extend session_links table with reconciliation fields."""
    logger.info("Starting migration: extend session_links with reconciliation fields")

    with get_session() as session:
        try:
            # Check if columns already exist
            check_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'session_links'
                AND column_name IN ('match_reason', 'deltas', 'resolved_at')
            """)
            existing = {row[0] for row in session.execute(check_query).fetchall()}

            # Add match_reason
            if "match_reason" not in existing:
                logger.info("Adding match_reason column")
                session.execute(text("""
                    ALTER TABLE session_links
                    ADD COLUMN match_reason JSONB
                """))

            # Add deltas
            if "deltas" not in existing:
                logger.info("Adding deltas column")
                session.execute(text("""
                    ALTER TABLE session_links
                    ADD COLUMN deltas JSONB
                """))

            # Add resolved_at
            if "resolved_at" not in existing:
                logger.info("Adding resolved_at column")
                session.execute(text("""
                    ALTER TABLE session_links
                    ADD COLUMN resolved_at TIMESTAMP WITH TIME ZONE
                """))

            session.commit()
            logger.info("Migration completed successfully")

        except Exception as e:
            session.rollback()
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_extend_session_links_reconciliation()
