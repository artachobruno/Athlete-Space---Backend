"""Backfill script for pairing unpaired activities with planned sessions.

This script processes activities that are NOT paired with planned sessions and attempts
to pair them using the auto-pairing service. This is useful after fixing pairing logic
issues (e.g., type mismatches) or when new pairing rules are added.

Usage:
    From project root:
    python scripts/backfill_unpaired_activities.py [--no-dry-run] [--user-id USER_ID] [--days DAYS]
    python scripts/backfill_unpaired_activities --relaxed [--no-dry-run] [--user-id USER_ID] [--days DAYS]

    Or as a module:
    python -m scripts.backfill_unpaired_activities [--no-dry-run] [--user-id USER_ID] [--days DAYS]
    python -m scripts.backfill_unpaired_activities --relaxed [--no-dry-run] [--user-id USER_ID] [--days DAYS]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
    - Can filter by user_id and days (recent activities only)

--relaxed:
    When standard pairing fails (e.g. duration_mismatch), use relaxed mode: pair when there is
    exactly one unpaired plan and one unpaired activity on the same day for the same sport.
    No duration check. Use this to fix "two cards per day" when auto-pairing cannot match.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
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
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_activity, get_link_for_planned, upsert_link

WORKOUT_TYPES = {
    "easy", "long", "threshold", "tempo", "interval", "vo2", "fartlek",
    "recovery", "rest", "race", "moderate", "hard", "quality", "hills",
    "strides", "aerobic", "steady", "marathon", "economy", "speed",
}
TYPE_TO_SPORT: dict[str, str] = {
    "running": "run", "run": "run", "ride": "ride", "bike": "ride",
    "cycling": "ride", "virtualride": "ride", "ebikeride": "ride",
    "swim": "swim", "swimming": "swim", "walk": "walk", "walking": "walk",
}


def _sport_key_plan(plan: PlannedSession) -> str:
    t = (plan.type or "").lower().strip()
    base = TYPE_TO_SPORT.get(t, t) if t else "run"
    return "run" if base in WORKOUT_TYPES else base


def _sport_key_activity(activity: Activity) -> str:
    t = (activity.sport or "").lower().strip()
    return TYPE_TO_SPORT.get(t, t) if t else "run"


def process_unpaired_activities(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Process unpaired activities and attempt to pair them.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back (if None, processes all)
        dry_run: If True, only log what would be done without making changes

    Returns:
        Dictionary with statistics about the processing
    """
    stats: dict[str, int] = {
        "activities_found": 0,
        "paired": 0,
        "failed": 0,
        "skipped": 0,
    }

    # Build query for all activities (we'll filter unpaired ones below)
    # Filter out activities with null user_id (invalid data that would cause pairing decision errors)
    query = select(Activity).where(
        Activity.user_id.isnot(None),
    )

    if user_id:
        query = query.where(Activity.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        query = query.where(Activity.starts_at >= cutoff_date)

    query = query.order_by(Activity.starts_at.desc())

    all_activities = list(db.scalars(query).all())

    # Schema v2: Filter to only unpaired activities (those without SessionLink)
    activities = []
    for activity in all_activities:
        link = get_link_for_activity(db, activity.id)
        if not link:
            activities.append(activity)

    stats["activities_found"] = len(activities)

    logger.info(
        f"Found {len(activities)} unpaired activities"
        f"{f' for user {user_id}' if user_id else ''}"
        f"{f' from last {days} days' if days else ''}"
    )

    for activity in activities:
        try:
            # Skip activities with null user_id (shouldn't happen due to query filter, but double-check)
            if not activity.user_id:
                logger.warning(f"Skipping activity {activity.id} - user_id is None")
                stats["skipped"] += 1
                continue

            activity_date = activity.starts_at.date() if activity.starts_at else None
            logger.debug(
                f"Processing activity {activity.id}: "
                f"sport={activity.sport}, date={activity_date}, "
                f"duration={activity.duration_seconds}s, "
                f"distance={activity.distance_meters}m"
            )

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would attempt to pair activity {activity.id} "
                    f"({activity.sport} on {activity_date}, "
                    f"duration={activity.duration_seconds}s, "
                    f"distance={activity.distance_meters}m)"
                )
                stats["skipped"] += 1
            else:
                # Attempt pairing
                try_auto_pair(activity=activity, session=db)
                db.commit()

                # Schema v2: Check if pairing succeeded by checking SessionLink
                db.refresh(activity)
                link = get_link_for_activity(db, activity.id)
                if link:
                    stats["paired"] += 1
                    logger.info(
                        f"✅ Paired activity {activity.id} with planned session {link.planned_session_id}"
                    )
                else:
                    stats["failed"] += 1
                    logger.debug(f"❌ Failed to pair activity {activity.id}")

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                f"Error processing activity {activity.id}: {e}",
                exc_info=True,
            )
            db.rollback()

    return stats


def process_relaxed_date_sport_1to1(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Pair by date+sport 1:1 when exactly one unpaired plan and one unpaired activity.

    No duration check. Use when standard backfill fails with duration_mismatch.

    Returns:
        Stats: pairs_found, paired, failed, skipped.
    """
    stats: dict[str, int] = {
        "pairs_found": 0,
        "paired": 0,
        "failed": 0,
        "skipped": 0,
    }

    day_start = datetime.now(UTC) - timedelta(days=days) if days else None

    query_a = select(Activity).where(Activity.user_id.isnot(None))
    if user_id:
        query_a = query_a.where(Activity.user_id == user_id)
    if day_start:
        query_a = query_a.where(Activity.starts_at >= day_start)
    all_activities = list(db.scalars(query_a).all())

    unpaired_activities = [a for a in all_activities if not get_link_for_activity(db, a.id)]
    if not unpaired_activities:
        logger.info("No unpaired activities for relaxed backfill")
        return stats

    seen_dates: set[tuple[str, date]] = set()
    for a in unpaired_activities:
        if not a.user_id or not a.starts_at:
            continue
        d = a.starts_at.date()
        seen_dates.add((a.user_id, d))
    if user_id:
        seen_dates = {(u, d) for u, d in seen_dates if u == user_id}

    logger.info(
        f"Relaxed backfill: {len(unpaired_activities)} unpaired activities, "
        f"{len(seen_dates)} (user, date) pairs to check"
    )

    for uid, d in sorted(seen_dates):
        day_begin = datetime.combine(d, datetime.min.time()).replace(tzinfo=UTC)
        day_end = datetime.combine(d, datetime.max.time()).replace(tzinfo=UTC)

        plans_q = (
            select(PlannedSession)
            .where(
                PlannedSession.user_id == uid,
                PlannedSession.starts_at >= day_begin,
                PlannedSession.starts_at <= day_end,
                PlannedSession.status.notin_(["cancelled", "deleted"]),
            )
        )
        plans_all = list(db.scalars(plans_q).all())
        plans = [p for p in plans_all if not get_link_for_planned(db, p.id)]

        acts = [
            a for a in unpaired_activities
            if a.user_id == uid and a.starts_at and a.starts_at.date() == d
        ]

        by_sport_plans: dict[str, list[PlannedSession]] = defaultdict(list)
        by_sport_acts: dict[str, list[Activity]] = defaultdict(list)
        for p in plans:
            by_sport_plans[_sport_key_plan(p)].append(p)
        for a in acts:
            by_sport_acts[_sport_key_activity(a)].append(a)

        for sport_key in set(by_sport_plans) & set(by_sport_acts):
            plist = by_sport_plans[sport_key]
            alist = by_sport_acts[sport_key]
            if len(plist) != 1 or len(alist) != 1:
                continue
            plan = plist[0]
            activity = alist[0]

            stats["pairs_found"] += 1
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would pair (relaxed) plan={plan.id} activity={activity.id} "
                    f"date={d} sport={sport_key}"
                )
                stats["skipped"] += 1
                continue

            try:
                upsert_link(
                    session=db,
                    user_id=uid,
                    planned_session_id=plan.id,
                    activity_id=activity.id,
                    status="confirmed",
                    method="manual",
                    notes="backfill_relaxed_date_sport_1to1",
                )
                db.commit()
                stats["paired"] += 1
                logger.info(
                    f"Paired (relaxed) plan={plan.id} activity={activity.id} date={d} sport={sport_key}"
                )
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"Failed to pair plan={plan.id} activity={activity.id}: {e}", exc_info=True)
                db.rollback()

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Backfill script to pair unpaired activities with planned sessions"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the pairing (default: dry-run mode)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user ID (optional)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only process activities from the last N days (optional)",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Pair by date+sport 1:1 only (no duration). Use when standard backfill fails.",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Backfill Unpaired Activities Script")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.relaxed:
        logger.info("Strategy: RELAXED (date+sport 1:1, no duration check)")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    if args.days:
        logger.info(f"Filter: last {args.days} days")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        if args.relaxed:
            stats = process_relaxed_date_sport_1to1(
                db=db,
                user_id=args.user_id,
                days=args.days,
                dry_run=dry_run,
            )
            logger.info("=" * 80)
            logger.info("Summary (relaxed):")
            logger.info(f"  1:1 pairs found: {stats['pairs_found']}")
            if dry_run:
                logger.info(f"  Would pair: {stats['pairs_found']}")
                logger.info("  (Run with --no-dry-run to actually pair)")
            else:
                logger.info(f"  Successfully paired: {stats['paired']}")
                logger.info(f"  Failed: {stats['failed']}")
            logger.info("=" * 80)
        else:
            stats = process_unpaired_activities(
                db=db,
                user_id=args.user_id,
                days=args.days,
                dry_run=dry_run,
            )
            logger.info("=" * 80)
            logger.info("Summary:")
            logger.info(f"  Activities found: {stats['activities_found']}")
            if dry_run:
                logger.info(f"  Would attempt to pair: {stats['activities_found']}")
                logger.info("  (Run with --no-dry-run to actually pair)")
            else:
                logger.info(f"  Successfully paired: {stats['paired']}")
                logger.info(f"  Failed to pair: {stats['failed']}")
            logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
