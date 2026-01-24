"""Backfill heat acclimation scores for existing activities.

Iterates chronologically per athlete and computes rolling HEU progressively.
Updates only activities with climate data.

Usage:
    python scripts/backfill_heat_acclimation.py --days 60
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta, timezone
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
from app.processing.heat_acclimation import (
    compute_effective_heat_stress_index,
    compute_heat_acclimation_score,
)


def backfill_heat_acclimation(days: int = 60) -> dict[str, int]:
    """Backfill heat acclimation scores for activities.

    Args:
        days: Number of days to look back from today

    Returns:
        Dictionary with counts of processed and updated activities
    """
    logger.info(f"Starting heat acclimation backfill for last {days} days")

    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    db: Session = SessionLocal()
    try:
        # Get all unique user_ids with activities in date range
        user_ids_result = db.execute(
            text(
                """
                SELECT DISTINCT user_id
                FROM activities
                WHERE starts_at >= :cutoff_date
                AND has_climate_data = TRUE
                AND heat_stress_index IS NOT NULL
                ORDER BY user_id
                """
            ),
            {"cutoff_date": cutoff_date},
        ).fetchall()

        user_ids = [row[0] for row in user_ids_result]
        logger.info(f"Found {len(user_ids)} users with climate data in date range")

        total_processed = 0
        total_updated = 0

        for user_id in user_ids:
            logger.info(f"Processing user {user_id}")

            # Get all activities for this user in chronological order
            activities = db.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.starts_at >= cutoff_date,
                    Activity.has_climate_data.is_(True),
                    Activity.heat_stress_index.isnot(None),
                    Activity.heat_stress_index >= 0.50,  # Only activities with meaningful heat
                )
                .order_by(Activity.starts_at.asc())
            ).scalars().all()

            logger.info(f"  Found {len(activities)} eligible activities for user {user_id}")

            for activity in activities:
                total_processed += 1

                try:
                    # Compute heat acclimation score (uses raw heat_stress_index from past activities)
                    heat_acclimation_score = compute_heat_acclimation_score(
                        session=db,
                        user_id=user_id,
                        activity_date=activity.starts_at,
                    )

                    # Compute effective HSI if we have a meaningful acclimation score
                    effective_heat_stress = None
                    if heat_acclimation_score > 0.0 and activity.heat_stress_index is not None:
                        effective_heat_stress = compute_effective_heat_stress_index(
                            heat_stress_index=activity.heat_stress_index,
                            heat_acclimation_score=heat_acclimation_score,
                        )

                    # Update activity if values changed
                    needs_update = False
                    if activity.heat_acclimation_score != heat_acclimation_score:
                        needs_update = True
                    if activity.effective_heat_stress_index != effective_heat_stress:
                        needs_update = True

                    if needs_update:
                        db.execute(
                            text(
                                """
                                UPDATE activities
                                SET heat_acclimation_score = :has,
                                    effective_heat_stress_index = :ehsi,
                                    climate_model_version = CASE
                                        WHEN :ehsi IS NOT NULL THEN 'v1.1'
                                        ELSE climate_model_version
                                    END
                                WHERE id = :activity_id
                                """
                            ),
                            {
                                "has": heat_acclimation_score if heat_acclimation_score > 0.0 else None,
                                "ehsi": effective_heat_stress,
                                "activity_id": activity.id,
                            },
                        )
                        total_updated += 1

                except Exception as e:
                    logger.warning(
                        f"Failed to process activity {activity.id} for user {user_id}: {e}"
                    )
                    continue

            # Commit after each user
            db.commit()
            logger.info(f"  Updated {total_updated} activities for user {user_id}")

        logger.info(
            f"Heat acclimation backfill complete: {total_processed} processed, {total_updated} updated"
        )

        return {
            "processed": total_processed,
            "updated": total_updated,
            "users": len(user_ids),
        }

    except Exception as e:
        logger.error(f"Error during heat acclimation backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill heat acclimation scores")
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Number of days to look back (default: 60)",
    )
    args = parser.parse_args()

    result = backfill_heat_acclimation(days=args.days)
    logger.info(f"Backfill result: {result}")
