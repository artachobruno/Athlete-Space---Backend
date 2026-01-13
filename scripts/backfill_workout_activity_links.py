"""Backfill script to link activities to workouts that have planned_session_id but no activity_id.

This script fixes the bug where reconciliation matched activities to planned sessions
but didn't update workout.activity_id. It finds orphaned workouts and links them to
their matching activities.

Usage:
    From project root:
    python scripts/backfill_workout_activity_links.py [--dry-run]

    Or as a module:
    python -m scripts.backfill_workout_activity_links [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.reconciliation_service import reconcile_calendar
from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.workouts.models import Workout


def backfill_workout_activity_links(*, dry_run: bool = False) -> dict[str, int]:
    """Backfill activity_id on workouts that have planned_session_id but no activity_id.

    Finds all workouts with planned_session_id set but activity_id IS NULL, then:
    1. Uses reconciliation to find matching activities
    2. Links the activity to the workout
    3. Updates workout distance/duration from activity

    Args:
        dry_run: If True, only report what would be done without making changes

    Returns:
        Dictionary with counts: {'processed', 'linked', 'skipped', 'errors'}
    """
    logger.info(f"Starting backfill for workout activity links (dry_run={dry_run})")

    stats: dict[str, int] = {
        "processed": 0,
        "linked": 0,
        "skipped": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        # Find all workouts with planned_session_id but no activity_id
        orphaned_workouts = db.execute(
            select(Workout).where(
                Workout.planned_session_id.isnot(None),
                Workout.activity_id.is_(None),
            )
        ).scalars().all()

        logger.info(f"Found {len(orphaned_workouts)} orphaned workouts")

        # Group by user_id for efficient reconciliation
        workouts_by_user: dict[str, list[Workout]] = {}
        for workout in orphaned_workouts:
            user_id = workout.user_id
            if user_id not in workouts_by_user:
                workouts_by_user[user_id] = []
            workouts_by_user[user_id].append(workout)

        # Process each user's workouts
        for user_id, workouts in workouts_by_user.items():
            logger.info(f"Processing {len(workouts)} workouts for user {user_id}")

            # Get athlete_id for reconciliation
            from app.db.models import StravaAccount
            account = db.execute(
                select(StravaAccount).where(StravaAccount.user_id == user_id)
            ).scalar_one_or_none()

            if not account:
                logger.warning(f"No StravaAccount found for user {user_id}, skipping")
                stats["skipped"] += len(workouts)
                continue

            athlete_id = int(account.athlete_id) if account.athlete_id else None
            if not athlete_id:
                logger.warning(f"No athlete_id for user {user_id}, skipping")
                stats["skipped"] += len(workouts)
                continue

            # Get date range for reconciliation (from all planned sessions)
            planned_session_ids = {w.planned_session_id for w in workouts if w.planned_session_id}
            planned_sessions = db.execute(
                select(PlannedSession).where(
                    PlannedSession.id.in_(planned_session_ids),
                    PlannedSession.user_id == user_id,
                )
            ).scalars().all()

            if not planned_sessions:
                logger.warning(f"No planned sessions found for workouts, skipping user {user_id}")
                stats["skipped"] += len(workouts)
                continue

            # Calculate date range
            dates = [ps.date.date() if isinstance(ps.date, datetime) else ps.date for ps in planned_sessions]
            start_date = min(dates) - timedelta(days=7)  # Add buffer
            end_date = max(dates) + timedelta(days=7)

            # Run reconciliation for this user
            try:
                reconciliation_results = reconcile_calendar(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as e:
                logger.error(f"Reconciliation failed for user {user_id}: {e}", exc_info=True)
                stats["errors"] += len(workouts)
                continue

            # Build map: planned_session_id -> matched_activity_id
            session_to_activity: dict[str, str] = {}
            for result in reconciliation_results:
                if result.matched_activity_id and result.status.value in {"completed", "partial"}:
                    session_to_activity[result.session_id] = result.matched_activity_id

            # Process each workout
            for workout in workouts:
                stats["processed"] += 1

                if not workout.planned_session_id:
                    stats["skipped"] += 1
                    continue

                # Find matching activity from reconciliation
                matched_activity_id = session_to_activity.get(workout.planned_session_id)

                if not matched_activity_id:
                    # Try to find via planned_session.completed_activity_id (fallback)
                    planned_session = db.execute(
                        select(PlannedSession).where(
                            PlannedSession.id == workout.planned_session_id,
                            PlannedSession.user_id == user_id,
                        )
                    ).scalar_one_or_none()

                    if planned_session and planned_session.completed_activity_id:
                        matched_activity_id = planned_session.completed_activity_id
                    else:
                        logger.debug(
                            "No matching activity found for workout",
                            workout_id=workout.id,
                            planned_session_id=workout.planned_session_id,
                        )
                        stats["skipped"] += 1
                        continue

                # Verify activity exists and belongs to user
                activity = db.execute(
                    select(Activity).where(
                        Activity.id == matched_activity_id,
                        Activity.user_id == user_id,
                    )
                ).scalar_one_or_none()

                if not activity:
                    logger.warning(
                        "Activity not found or doesn't belong to user",
                        activity_id=matched_activity_id,
                        workout_id=workout.id,
                        user_id=user_id,
                    )
                    stats["skipped"] += 1
                    continue

                if dry_run:
                    logger.info(
                        "[DRY RUN] Would link activity to workout",
                        workout_id=workout.id,
                        planned_session_id=workout.planned_session_id,
                        activity_id=matched_activity_id,
                        user_id=user_id,
                    )
                    stats["linked"] += 1
                    continue

                # Update workout
                try:
                    workout.activity_id = matched_activity_id

                    # Update workout distance/duration from actual activity
                    if activity.distance_meters is not None:
                        workout.total_distance_meters = int(activity.distance_meters)
                    if activity.duration_seconds is not None:
                        workout.total_duration_seconds = int(activity.duration_seconds)

                    # Update workout status
                    workout.status = "matched"

                    # Also update activity.workout_id if not set
                    if not activity.workout_id:
                        activity.workout_id = workout.id

                    db.flush()

                    logger.info(
                        "Linked activity to workout",
                        workout_id=workout.id,
                        planned_session_id=workout.planned_session_id,
                        activity_id=matched_activity_id,
                        user_id=user_id,
                    )
                    stats["linked"] += 1

                except Exception as e:
                    logger.error(
                        "Failed to link activity to workout",
                        workout_id=workout.id,
                        activity_id=matched_activity_id,
                        error=str(e),
                        exc_info=True,
                    )
                    db.rollback()
                    stats["errors"] += 1
                    continue

            # Commit after processing each user
            if not dry_run:
                try:
                    db.commit()
                    logger.info(f"Committed changes for user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to commit for user {user_id}: {e}", exc_info=True)
                    db.rollback()
                    stats["errors"] += len(workouts)

        logger.info(
            "Backfill completed",
            dry_run=dry_run,
            **stats,
        )

    except Exception as e:
        logger.error(f"Error during backfill: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()

    return stats


def main() -> int:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(description="Backfill activity_id on orphaned workouts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (don't make changes)",
    )
    args = parser.parse_args()

    try:
        stats = backfill_workout_activity_links(dry_run=args.dry_run)
        logger.info("Backfill script completed successfully", **stats)

        if args.dry_run:
            logger.info("DRY RUN MODE: No changes were made")
            logger.info(f"Would link {stats['linked']} workouts to activities")
            logger.info(f"Would skip {stats['skipped']} (no match found)")
        else:
            logger.info(f"Linked {stats['linked']} workouts to activities")
            logger.info(f"Skipped {stats['skipped']} (no match found)")
            if stats["errors"] > 0:
                logger.warning(f"Encountered {stats['errors']} errors")

        return 0 if stats["errors"] == 0 else 1

    except Exception as e:
        logger.exception("Backfill script failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
