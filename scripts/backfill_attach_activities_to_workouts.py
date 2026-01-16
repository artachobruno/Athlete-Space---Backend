"""Backfill script to attach historical activities to planned workouts.

This script scans historical activities and attaches them to planned workouts
when the intent is unambiguous. It follows strict matching rules to ensure
data integrity.

Matching Rules (ALL must pass):
- No existing attachment (activity not already linked to a workout)
- Same user (activity.user_id == workout.user_id)
- Same sport (normalized type matching: Run/run, Bike/ride, etc.)
- Time proximity (±90 minutes from workout creation)
- Duration similarity (±20%)
- Single candidate (exactly 1 workout match)

Usage:
    # Dry run (default)
    python scripts/backfill_attach_activities_to_workouts.py

    # Real run (set DRY_RUN = False in script)
    python scripts/backfill_attach_activities_to_workouts.py
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

# Add project root to Python path
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal

# Configuration
DRY_RUN = True
TIME_WINDOW_MINUTES = 90
DURATION_TOLERANCE = 0.20
MAX_MATCHES_PER_RUN: int | None = None  # Optional safety limit


def sport_types_match(activity_type: str | None, planned_type: str | None) -> bool:
    """Check if activity and planned session types match.

    Handles case-insensitive matching and common variations.
    Based on app/calendar/reconciliation.py _types_match function.

    Args:
        activity_type: Activity type (e.g., "run", "ride")
        planned_type: Planned session type (e.g., "Run", "Bike")

    Returns:
        True if types match, False otherwise
    """
    if not activity_type or not planned_type:
        return False

    planned_lower = planned_type.lower().strip()
    activity_lower = activity_type.lower().strip()

    # Exact match
    if planned_lower == activity_lower:
        return True

    # Common variations grouped by equivalence
    type_groups: list[list[str]] = [
        ["run", "running"],
        ["ride", "bike", "cycling", "virtualride"],
        ["swim", "swimming"],
        ["walk", "walking"],
    ]

    # Check if both types are in the same group
    return any(planned_lower in group and activity_lower in group for group in type_groups)


def duration_close(activity_duration: int | None, planned_duration_minutes: int | None) -> bool:
    """Check if activity duration is close to planned duration.

    Args:
        activity_duration: Activity duration in seconds
        planned_duration_minutes: Planned session duration in minutes

    Returns:
        True if durations are within tolerance, False otherwise
    """
    if not activity_duration or not planned_duration_minutes:
        return False

    planned_duration_seconds = planned_duration_minutes * 60
    max_duration = max(activity_duration, planned_duration_seconds)

    if max_duration == 0:
        return False

    difference = abs(activity_duration - planned_duration_seconds)
    ratio = difference / max_duration

    return ratio <= DURATION_TOLERANCE


def main() -> None:
    """Main function to backfill activity-to-workout attachments."""
    logger.info("Starting backfill: attach activities to workouts")
    logger.info(f"DRY_RUN={DRY_RUN}, TIME_WINDOW={TIME_WINDOW_MINUTES}min, DURATION_TOLERANCE={DURATION_TOLERANCE}")

    db: Session = SessionLocal()

    try:
        # Query unattached activities
        # Activities are unattached if they're not referenced by any PlannedSession.completed_activity_id
        # Use LEFT JOIN to find activities without a matching PlannedSession
        attached_ps = aliased(PlannedSession)
        unattached_activities = (
            db.execute(
                select(Activity)
                .outerjoin(attached_ps, Activity.id == attached_ps.completed_activity_id)
                .where(attached_ps.completed_activity_id.is_(None))
                .order_by(Activity.starts_at)
            )
            .scalars()
            .all()
        )

        logger.info(f"Found {len(unattached_activities)} unattached activities")

        matched = 0
        skipped = 0

        for activity in unattached_activities:
            # Skip if activity doesn't have required fields
            if not activity.start_time or not activity.duration_seconds or not activity.type:
                skipped += 1
                continue

            # Calculate time window
            time_window = timedelta(minutes=TIME_WINDOW_MINUTES)
            window_start = activity.start_time - time_window
            window_end = activity.start_time + time_window

            # Find candidate workouts (PlannedSessions) in time window
            candidates = (
                db.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == activity.user_id,
                        PlannedSession.created_at >= window_start,
                        PlannedSession.created_at <= window_end,
                        PlannedSession.completed_activity_id.is_(None),  # Not already completed
                    )
                )
                .scalars()
                .all()
            )

            # Filter by sport type match
            sport_matches = [w for w in candidates if sport_types_match(activity.type, w.type)]

            # Filter by duration similarity
            duration_matches = [
                w for w in sport_matches if duration_close(activity.duration_seconds, w.duration_minutes)
            ]

            # CRITICAL: Only proceed if exactly 1 match
            if len(duration_matches) != 1:
                skipped += 1
                continue

            workout = duration_matches[0]
            matched += 1

            logger.info(
                f"[MATCH] Activity {activity.id} → Workout {workout.id} "
                f"(type={activity.type}, duration={activity.duration_seconds}s, "
                f"start={activity.start_time})"
            )

            if not DRY_RUN:
                workout.completed_activity_id = activity.id
                db.add(workout)
                db.commit()
                logger.info(f"  ✓ Attached activity {activity.id} to workout {workout.id}")

            # Safety limit check
            if MAX_MATCHES_PER_RUN and matched >= MAX_MATCHES_PER_RUN:
                logger.info(f"Reached MAX_MATCHES_PER_RUN limit ({MAX_MATCHES_PER_RUN}), stopping")
                break

        logger.info("=" * 60)
        logger.info("Backfill complete:")
        logger.info(f"  Scanned: {len(unattached_activities)} activities")
        logger.info(f"  Matched: {matched}")
        logger.info(f"  Skipped: {skipped}")
        logger.info(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Error during backfill: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
