"""Bulk migration: Rebuild training load metrics for all users (one-time cleanup).

This script fixes EWMA corruption by:
1. Deleting all existing DailyTrainingLoad records for all users
2. Recomputing CTL, ATL, TSB from scratch using correct sequential EWMA
3. Ensuring write-once, append-only invariant (no historical overwrites)

Context:
Historical daily_training_load rows were being overwritten during recomputation,
corrupting the EWMA chain. EWMA requires strict chronological, append-only series.
Once a day's metrics are written, they must never change.

Usage:
    # Dry run (see what would be done, no changes)
    python scripts/migrate_training_load_all_users.py --dry-run

    # Run migration (rebuilds all users)
    python scripts/migrate_training_load_all_users.py

    # Run for specific user(s)
    python scripts/migrate_training_load_all_users.py --user-id <user_id>
    python scripts/migrate_training_load_all_users.py --user-id <user_id1> --user-id <user_id2>

Safety:
- Idempotent: Can be run multiple times safely
- Dry-run mode: Test without making changes
- Error handling: Continues with other users if one fails
- Validation: Checks recomputed metrics for correctness
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import delete, func, select

from app.db.models import Activity, DailyTrainingLoad
from app.db.session import get_session
from app.metrics.load_computation import (
    AthleteThresholds,
    compute_ctl_atl_form_from_tss,
    compute_daily_tss_load,
)


def get_all_user_ids() -> list[str]:
    """Get all unique user IDs that have activities.

    Returns:
        List of user IDs
    """
    with get_session() as session:
        result = session.execute(
            select(Activity.user_id).distinct().where(Activity.user_id.isnot(None))
        ).all()
        user_ids = [row[0] for row in result if row[0]]
        logger.info(f"Found {len(user_ids)} users with activities")
        return user_ids


def migrate_user_training_load(user_id: str) -> dict[str, int | str]:
    """Recompute training load metrics for a user from scratch.

    Note: All daily_training_load records should already be deleted before calling this.
    This function only recomputes and inserts new records.

    This ensures EWMA integrity by computing sequentially from the first activity
    to today, without any historical overwrites.

    Args:
        user_id: User ID to migrate

    Returns:
        Dictionary with counts and status
    """
    logger.info(f"Recomputing training load for user {user_id}")

    with get_session() as session:
        # Get date range from activities
        first_result = session.execute(
            select(func.min(Activity.starts_at)).where(Activity.user_id == user_id)
        ).scalar()

        if not first_result:
            logger.warning(f"No activities found for user {user_id}")
            return {
                "status": "skipped",
                "message": "No activities found",
                "created": 0,
            }

        first_date = first_result.date() if isinstance(first_result, datetime) else first_result
        end_date = datetime.now(UTC).date()

        logger.info(f"Date range: {first_date.isoformat()} to {end_date.isoformat()}")

        # Fetch all activities in date range
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.starts_at
                >= datetime.combine(first_date, datetime.min.time()).replace(tzinfo=UTC),
                Activity.starts_at
                <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC),
            )
            .order_by(Activity.starts_at)
        ).all()

        activity_list = [a[0] for a in activities]
        logger.info(f"Found {len(activity_list)} activities for user {user_id}")

        if not activity_list:
            logger.warning(f"No activities found for user {user_id} in date range")
            return {
                "status": "skipped",
                "message": "No activities in date range",
                "created": 0,
            }

        # Use default athlete thresholds
        athlete_thresholds = AthleteThresholds()

        # Compute daily TSS loads (aggregate activities → daily TSS)
        daily_tss_loads = compute_daily_tss_load(
            activity_list, first_date, end_date, athlete_thresholds
        )

        # Compute CTL, ATL, Form (FSB) from TSS (sequential EWMA computation)
        metrics = compute_ctl_atl_form_from_tss(daily_tss_loads, first_date, end_date)

        # Insert rows once, in chronological order
        daily_created = 0

        for date_val, tss_load in daily_tss_loads.items():
            metrics_for_date = metrics.get(date_val, {"ctl": 0.0, "atl": 0.0, "fsb": 0.0})

            form_value = metrics_for_date.get("fsb", 0.0)
            ctl_val = metrics_for_date["ctl"]
            atl_val = metrics_for_date["atl"]

            daily_load = DailyTrainingLoad(
                user_id=user_id,
                date=datetime.combine(date_val, datetime.min.time()).replace(tzinfo=UTC),
                ctl=ctl_val,
                atl=atl_val,
                tsb=form_value,  # Storing Form (FSB) in TSB column for backward compatibility
                load_score=tss_load,  # Daily TSS load
            )
            session.add(daily_load)
            daily_created += 1

        # Commit once at the end
        session.commit()

        logger.info(
            f"Recomputation complete for user {user_id}: "
            f"created={daily_created}, "
            f"date_range={first_date.isoformat()} to {end_date.isoformat()}"
        )

        return {
            "status": "success",
            "created": daily_created,
            "date_range": f"{first_date.isoformat()} to {end_date.isoformat()}",
        }


def validate_user_metrics(user_id: str) -> dict[str, bool | float | int]:
    """Validate migrated metrics for a user.

    Args:
        user_id: User ID

    Returns:
        Dictionary with validation results
    """
    with get_session() as session:
        # Get recent metrics
        recent_records = session.execute(
            select(DailyTrainingLoad)
            .where(DailyTrainingLoad.user_id == user_id)
            .order_by(DailyTrainingLoad.day.desc())
            .limit(14)
        ).all()

        if not recent_records:
            return {"has_data": False, "record_count": 0}

        records = [r[0] for r in recent_records]
        ctl_values = [r.ctl or 0.0 for r in records]
        atl_values = [r.atl or 0.0 for r in records]
        tsb_values = [r.tsb or 0.0 for r in records]

        # Basic validation checks
        ctl_not_constant = len(set(ctl_values)) > 1 if ctl_values else False
        atl_not_constant = len(set(atl_values)) > 1 if atl_values else False
        ctl_atl_different = any(
            abs(c - a) > 1.0 for c, a in zip(ctl_values, atl_values, strict=False)
        )

        # Check TSB range (should vary, not be constant)
        tsb_range = max(tsb_values) - min(tsb_values) if tsb_values else 0.0
        tsb_varies = tsb_range > 1.0

        return {
            "has_data": True,
            "record_count": len(records),
            "ctl_not_constant": ctl_not_constant,
            "atl_not_constant": atl_not_constant,
            "ctl_atl_different": ctl_atl_different,
            "tsb_varies": tsb_varies,
            "tsb_range": tsb_range,
            "recent_ctl": ctl_values[0] if ctl_values else 0.0,
            "recent_atl": atl_values[0] if atl_values else 0.0,
            "recent_tsb": tsb_values[0] if tsb_values else 0.0,
        }


def delete_all_training_load() -> int:
    """Delete all DailyTrainingLoad records for all users.

    Returns:
        Number of records deleted
    """
    with get_session() as session:
        result = session.execute(delete(DailyTrainingLoad))
        deleted_count = result.rowcount
        session.commit()
        logger.info(f"Deleted {deleted_count} DailyTrainingLoad records for all users")
        return deleted_count


def migrate_all_users(user_ids: list[str] | None = None, dry_run: bool = False) -> dict[str, int | dict]:
    """Migrate training load metrics for all users (or specified users).

    Strategy:
    1. Delete ALL daily_training_load records (clean slate)
    2. Recalculate all users sequentially

    Args:
        user_ids: Optional list of user IDs to migrate (if None, migrates all users)
        dry_run: If True, don't make changes, just report what would be done

    Returns:
        Dictionary with summary statistics
    """
    logger.info("=" * 80)
    logger.info("TRAINING LOAD MIGRATION (EWMA Corruption Fix)")
    logger.info("=" * 80)
    if dry_run:
        logger.warning("DRY RUN MODE - No changes will be made")

    if user_ids is None:
        user_ids = get_all_user_ids()
    else:
        logger.info(f"Migrating {len(user_ids)} specified user(s)")

    if not user_ids:
        logger.warning("No users found to migrate")
        return {
            "users_processed": 0,
            "users_succeeded": 0,
            "users_failed": 0,
            "users_skipped": 0,
            "total_deleted": 0,
            "total_created": 0,
            "validation_results": {},
        }

    # Step 1: Delete ALL daily_training_load records (clean slate)
    total_deleted = 0
    if dry_run:
        with get_session() as session:
            total_deleted = session.execute(
                select(func.count()).select_from(DailyTrainingLoad)
            ).scalar() or 0
        logger.info(f"[DRY RUN] Would delete {total_deleted} DailyTrainingLoad records for all users")
    else:
        logger.info("Step 1: Deleting all DailyTrainingLoad records...")
        total_deleted = delete_all_training_load()
        logger.info(f"Deleted {total_deleted} records - starting with clean slate")

    # Step 2: Recalculate all users
    total_created = 0
    users_succeeded = 0
    users_failed = 0
    users_skipped = 0
    validation_results: dict[str, dict[str, bool | float | int]] = {}

    logger.info(f"\nStep 2: Recalculating training load for {len(user_ids)} user(s)...")

    for idx, user_id in enumerate(user_ids, 1):
        logger.info(f"\n[{idx}/{len(user_ids)}] Processing user {user_id}")

        try:
            if dry_run:
                # Dry run: check if user has activities
                with get_session() as session:
                    activity_count = session.execute(
                        select(func.count(Activity.id)).where(Activity.user_id == user_id)
                    ).scalar() or 0

                logger.info(
                    f"[DRY RUN] Would recompute from {activity_count} activities for user {user_id}"
                )
                users_succeeded += 1
            else:
                # Run migration (delete already done, so just recompute)
                result = migrate_user_training_load(user_id)

                if result["status"] == "success":
                    created_count = result["created"]
                    if isinstance(created_count, int):
                        total_created += created_count
                    users_succeeded += 1

                    # Validate first 2 users
                    if users_succeeded <= 2:
                        validation = validate_user_metrics(user_id)
                        validation_results[user_id] = validation
                        logger.info(f"Validation for {user_id}: {validation}")

                elif result["status"] == "skipped":
                    users_skipped += 1
                    logger.info(f"Skipped user {user_id}: {result.get('message', 'Unknown reason')}")

        except Exception as e:
            logger.error(f"Error processing user {user_id}: {e}", exc_info=True)
            users_failed += 1

    summary = {
        "users_processed": len(user_ids),
        "users_succeeded": users_succeeded,
        "users_failed": users_failed,
        "users_skipped": users_skipped,
        "total_deleted": total_deleted,
        "total_created": total_created,
        "validation_results": validation_results,
    }

    logger.info("\n" + "=" * 80)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Users processed: {len(user_ids)}")
    logger.info(f"Users succeeded: {users_succeeded}")
    logger.info(f"Users failed: {users_failed}")
    logger.info(f"Users skipped: {users_skipped}")
    if not dry_run:
        logger.info(f"Total records deleted: {total_deleted}")
        logger.info(f"Total records created: {total_created}")
        logger.info(f"Validation results: {len(validation_results)} users validated")

    if validation_results:
        logger.info("\nValidation Details:")
        for user_id, validation in validation_results.items():
            logger.info(f"  User {user_id}:")
            logger.info(f"    Records: {validation.get('record_count', 0)}")
            logger.info(f"    CTL not constant: {validation.get('ctl_not_constant', False)}")
            logger.info(f"    ATL not constant: {validation.get('atl_not_constant', False)}")
            logger.info(f"    CTL/ATL different: {validation.get('ctl_atl_different', False)}")
            logger.info(f"    TSB varies: {validation.get('tsb_varies', False)}")
            logger.info(f"    Recent CTL: {validation.get('recent_ctl', 0.0):.2f}")
            logger.info(f"    Recent ATL: {validation.get('recent_atl', 0.0):.2f}")
            logger.info(f"    Recent TSB: {validation.get('recent_tsb', 0.0):.2f}")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate training load metrics for all users (EWMA corruption fix)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode: don't make changes, just report what would be done",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        dest="user_ids",
        help="Specific user ID(s) to migrate (can be specified multiple times). "
        "If not specified, migrates all users.",
    )
    args = parser.parse_args()

    try:
        result = migrate_all_users(user_ids=args.user_ids, dry_run=args.dry_run)
        if args.dry_run:
            logger.info("\n✅ Dry run completed. Run without --dry-run to apply changes.")
        else:
            logger.info("\n✅ Migration completed!")
            users_failed = result["users_failed"]
            if isinstance(users_failed, int) and users_failed > 0:
                logger.warning(f"⚠️  {users_failed} user(s) failed - check logs above")
            sys.exit(0 if isinstance(users_failed, int) and users_failed == 0 else 1)
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Migration interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)
