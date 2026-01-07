"""Add composite database indexes for common query patterns.

This script adds composite indexes to optimize queries that filter by
user_id + start_time, which is a very common pattern in the application.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def add_composite_indexes() -> None:
    """Add composite indexes for common query patterns."""
    logger.info("Adding composite indexes for query optimization...")

    with engine.connect() as conn:
        try:
            if _is_postgresql():
                # PostgreSQL: Create composite indexes
                indexes = [
                    {
                        "name": "idx_activities_user_start_time",
                        "table": "activities",
                        "columns": "(user_id, start_time DESC)",
                        "description": "Optimizes queries filtering by user_id and ordering by start_time",
                    },
                    {
                        "name": "idx_activities_user_start_streams",
                        "table": "activities",
                        "columns": "(user_id, start_time DESC) WHERE streams_data IS NULL",
                        "description": "Optimizes queries for activities without streams data",
                    },
                ]

                for idx in indexes:
                    try:
                        conn.execute(
                            text(
                                f"""
                                CREATE INDEX IF NOT EXISTS {idx["name"]}
                                ON {idx["table"]} {idx["columns"]}
                                """
                            )
                        )
                        logger.info(f"Created index: {idx['name']} - {idx['description']}")
                    except Exception as e:
                        logger.warning(f"Failed to create index {idx['name']}: {e}")

                conn.commit()
                logger.info("Composite indexes added successfully")
            else:
                # SQLite: Create composite indexes (simpler syntax)
                indexes = [
                    {
                        "name": "idx_activities_user_start_time",
                        "table": "activities",
                        "columns": "(user_id, start_time DESC)",
                        "description": "Optimizes queries filtering by user_id and ordering by start_time",
                    },
                ]

                for idx in indexes:
                    try:
                        conn.execute(
                            text(
                                f"""
                                CREATE INDEX IF NOT EXISTS {idx["name"]}
                                ON {idx["table"]} {idx["columns"]}
                                """
                            )
                        )
                        logger.info(f"Created index: {idx['name']} - {idx['description']}")
                    except Exception as e:
                        logger.warning(f"Failed to create index {idx['name']}: {e}")

                conn.commit()
                logger.info("Composite indexes added successfully (SQLite)")

        except Exception as e:
            logger.error(f"Error adding composite indexes: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    add_composite_indexes()
