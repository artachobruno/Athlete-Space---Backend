"""Backfill cold stress (wind chill and CSI) for existing activities.

Only processes activities where wind + temp exist.
Safe to run multiple times (idempotent).

Usage:
    python scripts/backfill_cold_stress.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.db.session import SessionLocal
from app.processing.activity_climate_aggregator import aggregate_activity_climate


def backfill_cold_stress() -> dict[str, int]:
    """Backfill cold stress for activities with climate data.

    Returns:
        Dictionary with counts of processed and updated activities
    """
    logger.info("Starting cold stress backfill")

    db: Session = SessionLocal()
    try:
        # Find activities with climate data but missing cold stress fields
        activities = db.execute(
            select(Activity).where(
                Activity.has_climate_data.is_(True),
                Activity.avg_temperature_c.isnot(None),
                Activity.wind_avg_mps.isnot(None),
            )
        ).scalars().all()

        logger.info(f"Found {len(activities)} activities with climate data")

        total_processed = 0
        total_updated = 0

        for activity in activities:
            total_processed += 1

            try:
                # Use existing aggregation function which handles cold stress
                if aggregate_activity_climate(db, activity):
                    total_updated += 1
            except Exception as e:
                logger.warning(f"Failed to process activity {activity.id}: {e}")
                continue

        db.commit()
        logger.info(
            f"Cold stress backfill complete: {total_processed} processed, {total_updated} updated"
        )

    except Exception as e:
        logger.error(f"Error during cold stress backfill: {e}")
        db.rollback()
        raise
    else:
        return {
            "processed": total_processed,
            "updated": total_updated,
        }
    finally:
        db.close()


if __name__ == "__main__":
    result = backfill_cold_stress()
    logger.info(f"Backfill result: {result}")
