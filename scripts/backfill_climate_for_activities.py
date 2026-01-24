"""Backfill climate data for existing activities.

One-off job to add climate data to activities that were ingested before
climate sampling was implemented.

Usage:
    python scripts/backfill_climate_for_activities.py --since 2024-01-01
    python scripts/backfill_climate_for_activities.py --since 2024-01-01 --limit 100
    python scripts/backfill_climate_for_activities.py --since 2024-01-01 --rate-limit 2.0
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timezone
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
from app.ingestion.climate_sampling import sample_activity_climate
from app.integrations.weather.client import get_weather_client
from app.processing.activity_climate_aggregator import aggregate_activity_climate


def _is_indoor_activity(activity: Activity) -> bool:
    """Check if activity is likely indoor (no GPS data).

    Args:
        activity: Activity record

    Returns:
        True if activity appears to be indoor
    """
    streams_data = activity.metrics.get("streams_data") if activity.metrics else None
    if not streams_data:
        return True

    # Check for GPS data
    latlng_data = None
    if "latlng" in streams_data:
        latlng_stream = streams_data["latlng"]
        if isinstance(latlng_stream, dict) and "data" in latlng_stream:
            latlng_data = latlng_stream["data"]
        elif isinstance(latlng_stream, list):
            latlng_data = latlng_stream

    return not latlng_data or len(latlng_data) == 0


def backfill_climate_for_activities(
    since_date: datetime,
    limit: int | None = None,
    rate_limit_seconds: float = 1.0,
) -> dict[str, int]:
    """Backfill climate data for activities since a given date.

    Rules:
    - Skip activities < 30 min
    - Skip indoor activities (no GPS)
    - Rate-limit weather API calls
    - Log failures, don't crash

    Args:
        since_date: Only process activities after this date
        limit: Maximum number of activities to process (None = no limit)
        rate_limit_seconds: Seconds to wait between weather API calls

    Returns:
        Dictionary with counts: processed, sampled, aggregated, skipped, failed
    """
    logger.info(f"Starting climate backfill for activities since {since_date.isoformat()}")

    db = SessionLocal()
    weather_client = get_weather_client()

    if not weather_client.api_key:
        logger.error("Weather API key not configured. Cannot backfill climate data.")
        return {
            "processed": 0,
            "sampled": 0,
            "aggregated": 0,
            "skipped": 0,
            "failed": 0,
        }

    try:
        # Find activities that need climate data
        query = (
            select(Activity)
            .where(
                Activity.starts_at >= since_date,
                Activity.has_climate_data.is_(False) | Activity.has_climate_data.is_(None),
            )
            .order_by(Activity.starts_at.desc())
        )

        if limit:
            query = query.limit(limit)

        activities = db.execute(query).scalars().all()

        if not activities:
            logger.info("No activities found needing climate backfill")
            return {
                "processed": 0,
                "sampled": 0,
                "aggregated": 0,
                "skipped": 0,
                "failed": 0,
            }

        logger.info(f"Found {len(activities)} activities to process")

        stats = {
            "processed": 0,
            "sampled": 0,
            "aggregated": 0,
            "skipped": 0,
            "failed": 0,
        }

        for activity in activities:
            try:
                stats["processed"] += 1

                # Skip activities < 30 min
                if not activity.duration_seconds or activity.duration_seconds < 30 * 60:
                    logger.debug(f"Skipping activity {activity.id}: duration < 30 min")
                    stats["skipped"] += 1
                    continue

                # Skip indoor activities (no GPS)
                if _is_indoor_activity(activity):
                    logger.debug(f"Skipping activity {activity.id}: no GPS data (indoor)")
                    stats["skipped"] += 1
                    continue

                # Sample climate data
                logger.info(f"Sampling climate for activity {activity.id} ({activity.starts_at})")
                samples_count = sample_activity_climate(db, activity, weather_client)

                if samples_count > 0:
                    stats["sampled"] += 1
                    db.commit()  # Commit samples first

                    # Aggregate climate data
                    if aggregate_activity_climate(db, activity):
                        stats["aggregated"] += 1
                        db.commit()

                        logger.info(
                            f"Successfully backfilled climate for activity {activity.id}: "
                            f"{samples_count} samples, label={activity.conditions_label}"
                        )
                    else:
                        logger.warning(f"Failed to aggregate climate for activity {activity.id}")
                        stats["failed"] += 1
                else:
                    logger.warning(f"No climate samples collected for activity {activity.id}")
                    stats["failed"] += 1

                # Rate-limit weather API calls
                if rate_limit_seconds > 0:
                    time.sleep(rate_limit_seconds)

            except Exception as e:
                logger.error(f"Error processing activity {activity.id}: {e}")
                stats["failed"] += 1
                db.rollback()
                continue

        logger.info(
            f"Climate backfill complete: processed={stats['processed']}, "
            f"sampled={stats['sampled']}, aggregated={stats['aggregated']}, "
            f"skipped={stats['skipped']}, failed={stats['failed']}"
        )

        return stats

    finally:
        db.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Backfill climate data for existing activities")
    parser.add_argument(
        "--since",
        type=str,
        required=True,
        help="Start date (ISO format: YYYY-MM-DD)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of activities to process (default: no limit)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Seconds to wait between weather API calls (default: 1.0)",
    )

    args = parser.parse_args()

    # Parse date
    try:
        since_date = datetime.fromisoformat(args.since)
        if since_date.tzinfo is None:
            since_date = since_date.replace(tzinfo=UTC)
    except ValueError:
        logger.error(f"Invalid date format: {args.since}. Use YYYY-MM-DD format.")
        sys.exit(1)

    stats = backfill_climate_for_activities(
        since_date=since_date,
        limit=args.limit,
        rate_limit_seconds=args.rate_limit,
    )

    logger.info("Backfill completed successfully")
    logger.info(f"Stats: {stats}")


if __name__ == "__main__":
    main()
