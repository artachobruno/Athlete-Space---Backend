"""Script to detect and fix mis-paired activities.

For each paired activity, checks if there's a better match (by duration + time proximity)
and re-pairs if needed.

Usage:
    python scripts/fix_mispaired_activities.py [--no-dry-run] [--user-id USER_ID] [--dates DATE1,DATE2,...]
    python scripts/fix_mispaired_activities.py --dates 2026-01-19,2026-01-22,2026-01-23 --no-dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
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
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.pairing.auto_pairing_service import DURATION_TOLERANCE, _types_match
from app.pairing.session_links import (
    get_link_for_activity,
    get_link_for_planned,
    unlink_by_activity,
    upsert_link,
)


def _calculate_time_diff(activity: Activity, plan: PlannedSession) -> int:
    """Calculate time difference in minutes between activity and plan."""
    if not activity.starts_at or not plan.starts_at:
        return 9999
    activity_time = activity.starts_at.time()
    plan_time = plan.starts_at.time()
    activity_minutes = activity_time.hour * 60 + activity_time.minute
    plan_minutes = plan_time.hour * 60 + plan_time.minute
    return abs(activity_minutes - plan_minutes)


def _find_best_match(
    activity: Activity,
    plans: list[PlannedSession],
) -> tuple[PlannedSession, float, int] | None:
    """Find the best matching planned session for an activity.

    Returns:
        Tuple of (best_plan, duration_diff_pct, time_diff_minutes) or None if no match
    """
    if not activity.duration_seconds:
        return None

    activity_duration_minutes = activity.duration_seconds / 60.0
    matches = []

    for plan in plans:
        if not plan.duration_minutes:
            continue

        if not _types_match(plan.type, activity.sport):
            continue

        diff_minutes = abs(plan.duration_minutes - activity_duration_minutes)
        diff_pct = diff_minutes / plan.duration_minutes

        if diff_pct <= DURATION_TOLERANCE:
            time_diff = _calculate_time_diff(activity, plan)
            matches.append((diff_pct, time_diff, plan))

    if not matches:
        return None

    # Sort by: duration diff (smallest first), then time diff, then created_at
    matches.sort(key=lambda x: (x[0], x[1], x[2].created_at, x[2].id))
    best_match = matches[0]
    return (best_match[2], best_match[0], best_match[1])


def fix_mispaired_for_date(
    db: Session,
    user_id: str,
    target_date: date,
    dry_run: bool = True,
) -> dict[str, int]:
    """Fix mis-paired activities for a specific date.

    Returns:
        Stats dict with: checked, fixed, already_optimal, no_better_match
    """
    stats: dict[str, int] = {
        "checked": 0,
        "fixed": 0,
        "already_optimal": 0,
        "no_better_match": 0,
    }

    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=UTC)
    day_end = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=UTC)

    # Get all activities on this date
    activities_query = (
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.starts_at >= day_start,
            Activity.starts_at <= day_end,
        )
    )
    activities = list(db.scalars(activities_query).all())

    # Get all planned sessions on this date
    plans_query = (
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= day_start,
            PlannedSession.starts_at <= day_end,
            PlannedSession.status.notin_(["cancelled", "deleted"]),
        )
    )
    all_plans = list(db.scalars(plans_query).all())

    logger.info(
        f"Date {target_date}: {len(activities)} activities, {len(all_plans)} planned sessions"
    )

    for activity in activities:
        current_link = get_link_for_activity(db, activity.id)
        if not current_link:
            continue  # Skip unpaired activities

        stats["checked"] += 1
        current_plan_id = current_link.planned_session_id

        # Get current planned session
        current_plan = next((p for p in all_plans if p.id == current_plan_id), None)
        if not current_plan:
            logger.warning(
                f"Activity {activity.id} is paired to plan {current_plan_id} which is not on this date"
            )
            continue

        # Find best match among ALL plans (including currently paired one)
        best_match = _find_best_match(activity, all_plans)

        if not best_match:
            stats["no_better_match"] += 1
            logger.debug(
                f"Activity {activity.id}: No valid match found (duration mismatch or type mismatch)"
            )
            continue

        best_plan, best_duration_diff, best_time_diff = best_match

        if best_plan.id == current_plan_id:
            stats["already_optimal"] += 1
            logger.debug(
                f"Activity {activity.id}: Already paired to best match (plan {current_plan_id})"
            )
            continue

        # Check if the new match is significantly better
        current_duration_diff = (
            abs(current_plan.duration_minutes - (activity.duration_seconds / 60.0))
            / current_plan.duration_minutes
            if current_plan.duration_minutes
            else 9999
        )
        current_time_diff = _calculate_time_diff(activity, current_plan)

        # Only re-pair if significantly better (better duration match OR much closer time)
        is_better = (
            best_duration_diff < current_duration_diff * 0.8  # 20% better duration match
            or (best_time_diff < current_time_diff - 60)  # At least 1 hour closer in time
        )

        if not is_better:
            stats["no_better_match"] += 1
            logger.debug(
                f"Activity {activity.id}: Current pairing is acceptable "
                f"(current: {current_duration_diff:.2%} duration, {current_time_diff}min time; "
                f"best: {best_duration_diff:.2%} duration, {best_time_diff}min time)"
            )
            continue

        # Re-pair to better match
        stats["fixed"] += 1
        logger.info(
            f"Activity {activity.id}: Re-pairing from plan {current_plan_id} "
            f"to {best_plan.id} (better match: {best_duration_diff:.2%} duration diff, "
            f"{best_time_diff}min time diff)"
        )

        if not dry_run:
            # Unlink current pairing
            unlink_by_activity(db, activity.id, reason="fix_mispaired_better_match")
            db.flush()

            # Create new link
            upsert_link(
                session=db,
                user_id=user_id,
                planned_session_id=best_plan.id,
                activity_id=activity.id,
                status="confirmed",
                method="manual",
                notes=f"fix_mispaired: better match (duration {best_duration_diff:.2%}, time {best_time_diff}min)",
            )
            db.commit()

    return stats


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fix mis-paired activities by finding better matches"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the re-pairing (default: dry-run mode)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user ID (optional)",
    )
    parser.add_argument(
        "--dates",
        type=str,
        default=None,
        help="Comma-separated dates (YYYY-MM-DD) to check, e.g., 2026-01-19,2026-01-22,2026-01-23",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    # Parse dates
    target_dates: list[date] = []
    if args.dates:
        for date_str in args.dates.split(","):
            try:
                target_dates.append(datetime.strptime(date_str.strip(), "%Y-%m-%d").date())
            except ValueError as e:
                logger.error(f"Invalid date format: {date_str.strip()} ({e})")
                sys.exit(1)
    else:
        # Default: last 30 days
        today = datetime.now(UTC).date()
        for i in range(30):
            target_dates.append(today - timedelta(days=i))

    logger.info("=" * 80)
    logger.info("Fix Mis-Paired Activities")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    logger.info(f"Dates to check: {len(target_dates)} dates")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        # Get user IDs to process
        if args.user_id:
            user_ids = [args.user_id]
        else:
            logger.error("--user-id is required")
            sys.exit(1)

        total_stats: dict[str, int] = {
            "checked": 0,
            "fixed": 0,
            "already_optimal": 0,
            "no_better_match": 0,
        }

        for user_id in user_ids:
            logger.info(f"Processing user {user_id}")
            for target_date in target_dates:
                stats = fix_mispaired_for_date(
                    db=db,
                    user_id=user_id,
                    target_date=target_date,
                    dry_run=dry_run,
                )
                for key in total_stats:
                    total_stats[key] += stats[key]

        logger.info("=" * 80)
        logger.info("FINAL SUMMARY")
        logger.info("=" * 80)
        logger.info(f"  Activities checked: {total_stats['checked']}")
        logger.info(f"  Already optimal: {total_stats['already_optimal']}")
        logger.info(f"  No better match found: {total_stats['no_better_match']}")
        if dry_run:
            logger.info(f"  Would fix: {total_stats['fixed']}")
            logger.info("  (Run with --no-dry-run to actually fix)")
        else:
            logger.info(f"  Successfully fixed: {total_stats['fixed']}")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
