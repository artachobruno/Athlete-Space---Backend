"""Backfill script to rename generic Strava activity titles.

This script processes ALL activities with generic Strava titles like:
- Morning Run, Lunch Run, Afternoon Run, Evening Run, Night Run
- Morning Ride, Lunch Ride, etc.

And renames them based on activity metrics (distance, duration) to more descriptive titles.

Usage:
    From project root:
    python scripts/backfill_activity_titles.py [--no-dry-run] [--limit N]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
"""

from __future__ import annotations

import argparse
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
from sqlalchemy import select

from app.db.models import Activity
from app.db.session import SessionLocal


def _is_generic_strava_title(title: str | None) -> bool:
    """Check if title is a generic Strava-style auto-generated title.

    Args:
        title: Title to check

    Returns:
        True if title is generic/auto-generated
    """
    if not title:
        return True

    title_lower = title.lower().strip()

    # Time-of-day prefixes used by Strava
    time_prefixes = ["morning", "lunch", "afternoon", "evening", "night"]

    # Activity types used by Strava
    activity_types = [
        "run", "ride", "swim", "walk", "hike", "workout",
        "weight training", "yoga", "crossfit", "elliptical",
        "stair stepper", "rowing", "ski", "snowboard",
        "ice skate", "kayak", "surf", "windsurf", "kitesurf",
    ]

    # Check for "Time Activity" pattern (e.g., "Morning Run", "Lunch Swim")
    for prefix in time_prefixes:
        for activity in activity_types:
            if title_lower == f"{prefix} {activity}":
                return True

    # Also catch simple generic titles
    generic_exact = {
        "run", "running", "ride", "cycling", "swim", "swimming",
        "activity", "workout", "exercise", "training",
    }
    return title_lower in generic_exact


def _generate_title_from_activity(activity: Activity) -> str:
    """Generate a descriptive title from activity metrics.

    Uses distance, duration, and sport to create meaningful titles like:
    - "5K Run" for ~5km runs
    - "10K Run" for ~10km runs  
    - "Half Marathon" for ~21km runs
    - "Long Run (15 mi)" for long runs
    - "Easy Run" for short easy runs
    - "Quick Run" for short duration runs

    Args:
        activity: The activity to generate title for

    Returns:
        Descriptive title string
    """
    sport = (activity.sport or "run").lower()
    distance_m = activity.distance_meters or 0
    duration_sec = activity.duration_seconds or 0

    # Convert to km and miles
    distance_km = distance_m / 1000.0
    distance_mi = distance_km * 0.621371
    duration_min = duration_sec / 60.0

    # Sport-specific title generation
    if sport in ("run", "running"):
        return _generate_run_title(distance_km, distance_mi, duration_min)
    elif sport in ("ride", "cycling", "bike"):
        return _generate_ride_title(distance_km, distance_mi, duration_min)
    elif sport in ("swim", "swimming"):
        return _generate_swim_title(distance_m, duration_min)
    elif sport in ("walk", "walking"):
        return _generate_walk_title(distance_km, duration_min)
    elif sport in ("hike", "hiking"):
        return _generate_hike_title(distance_km, duration_min)
    else:
        return _generate_generic_title(sport, duration_min)


def _generate_run_title(distance_km: float, distance_mi: float, duration_min: float) -> str:
    """Generate title for running activities."""
    # Check for race distances first
    if 4.8 <= distance_km <= 5.2:
        return "5K Run"
    elif 9.8 <= distance_km <= 10.2:
        return "10K Run"
    elif 14.8 <= distance_km <= 15.2:
        return "15K Run"
    elif 20.5 <= distance_km <= 21.5:
        return "Half Marathon"
    elif 41.5 <= distance_km <= 43.0:
        return "Marathon"

    # Distance-based titles
    if distance_km >= 20:
        return f"Long Run ({distance_km:.0f}K)"
    elif distance_km >= 15:
        return f"Long Run ({distance_mi:.0f} mi)"
    elif distance_km >= 10:
        return f"Steady Run ({distance_km:.0f}K)"
    elif distance_km >= 5:
        return f"Easy Run ({distance_km:.1f}K)"
    elif distance_km >= 2:
        return "Short Run"
    elif duration_min >= 20:
        return f"Run ({duration_min:.0f} min)"
    else:
        return "Quick Run"


def _generate_ride_title(distance_km: float, distance_mi: float, duration_min: float) -> str:
    """Generate title for cycling activities."""
    if distance_km >= 100:
        return f"Century Ride ({distance_km:.0f}K)"
    elif distance_km >= 50:
        return f"Long Ride ({distance_km:.0f}K)"
    elif distance_km >= 30:
        return f"Ride ({distance_km:.0f}K)"
    elif distance_km >= 15:
        return f"Easy Ride ({distance_km:.0f}K)"
    elif duration_min >= 30:
        return f"Ride ({duration_min:.0f} min)"
    else:
        return "Quick Ride"


def _generate_swim_title(distance_m: float, duration_min: float) -> str:
    """Generate title for swimming activities."""
    if distance_m >= 3800:
        return "Iron Distance Swim"
    elif distance_m >= 1900:
        return "Half Iron Swim"
    elif distance_m >= 1500:
        return "Olympic Swim"
    elif distance_m >= 750:
        return "Sprint Swim"
    elif distance_m >= 400:
        return f"Swim ({distance_m:.0f}m)"
    elif duration_min >= 20:
        return f"Swim ({duration_min:.0f} min)"
    else:
        return "Quick Swim"


def _generate_walk_title(distance_km: float, duration_min: float) -> str:
    """Generate title for walking activities."""
    if distance_km >= 10:
        return f"Long Walk ({distance_km:.0f}K)"
    elif distance_km >= 5:
        return f"Walk ({distance_km:.1f}K)"
    elif duration_min >= 30:
        return f"Walk ({duration_min:.0f} min)"
    else:
        return "Short Walk"


def _generate_hike_title(distance_km: float, duration_min: float) -> str:
    """Generate title for hiking activities."""
    if distance_km >= 15:
        return f"Long Hike ({distance_km:.0f}K)"
    elif distance_km >= 8:
        return f"Hike ({distance_km:.0f}K)"
    elif duration_min >= 60:
        hours = duration_min / 60
        return f"Hike ({hours:.1f} hrs)"
    else:
        return "Short Hike"


def _generate_generic_title(sport: str, duration_min: float) -> str:
    """Generate title for other activity types."""
    sport_display = sport.replace("_", " ").title()
    if duration_min >= 60:
        hours = duration_min / 60
        return f"{sport_display} ({hours:.1f} hrs)"
    elif duration_min >= 10:
        return f"{sport_display} ({duration_min:.0f} min)"
    else:
        return sport_display


def backfill_activity_titles(
    dry_run: bool = True,
    limit: int = 0,
) -> dict[str, int]:
    """Backfill generic Strava titles for all activities.

    Args:
        dry_run: If True, only log what would be done (default: True)
        limit: Maximum number of activities to process (0 = no limit)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "activities_found": 0,
        "titles_updated": 0,
        "already_good": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(f"Starting activity title backfill (dry_run={dry_run}, limit={limit})")

        # Find all activities
        query = select(Activity).order_by(Activity.starts_at.desc())
        if limit > 0:
            query = query.limit(limit)

        activities = db.execute(query).scalars().all()

        logger.info(f"Found {len(activities)} activities to check")
        stats["activities_found"] = len(activities)

        for i, activity in enumerate(activities):
            try:
                if not _is_generic_strava_title(activity.title):
                    stats["already_good"] += 1
                    continue

                new_title = _generate_title_from_activity(activity)

                # Don't update if title would be the same
                if new_title.lower() == (activity.title or "").lower():
                    stats["already_good"] += 1
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Activity {activity.id}: "
                        f"'{activity.title}' -> '{new_title}'"
                    )
                else:
                    old_title = activity.title
                    activity.title = new_title
                    logger.info(f"Updated {activity.id}: '{old_title}' -> '{new_title}'")

                stats["titles_updated"] += 1

                # Log progress every 500 activities
                if (i + 1) % 500 == 0:
                    logger.info(f"Progress: {i + 1}/{len(activities)} activities processed")

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error processing activity {activity.id}: {e}")
                continue

        if not dry_run:
            db.commit()
            logger.info("Backfill complete - changes committed")
        else:
            logger.info("DRY RUN complete - no changes made")

        logger.info(
            f"Activity title backfill complete: "
            f"dry_run={dry_run}, "
            f"activities_found={stats['activities_found']}, "
            f"titles_updated={stats['titles_updated']}, "
            f"already_good={stats['already_good']}, "
            f"errors={stats['errors']}"
        )
    except Exception as e:
        db.rollback()
        logger.exception(f"Fatal error in backfill: {e}")
        raise
    else:
        return stats
    finally:
        db.close()


def main() -> int:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(
        description="Backfill generic Strava activity titles",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the backfill (default: dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of activities to process (0 = no limit)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    try:
        stats = backfill_activity_titles(dry_run=dry_run, limit=args.limit)
        logger.info(f"Backfill completed successfully: {stats}")
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
