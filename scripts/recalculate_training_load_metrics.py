"""Recalculate training load metrics for all users.

This script:
1. Deletes all existing DailyTrainingLoad records
2. Recomputes CTL, ATL, TSB from scratch using the new TSS-based methodology
3. Processes all users from their first activity to today
4. Uses the same computation functions as the runtime system
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
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
        result = session.execute(select(Activity.user_id).distinct().where(Activity.user_id.isnot(None))).all()
        user_ids = [row[0] for row in result if row[0]]
        logger.info(f"Found {len(user_ids)} users with activities")
        return user_ids


def get_user_activity_date_range(user_id: str) -> tuple[date | None, date]:
    """Get the date range for a user's activities.

    Args:
        user_id: User ID

    Returns:
        Tuple of (first_activity_date, today)
        Returns (None, today) if no activities found
    """
    with get_session() as session:
        # Get first activity date
        first_result = session.execute(select(func.min(Activity.start_time)).where(Activity.user_id == user_id)).scalar()

        if not first_result:
            return None, datetime.now(UTC).date()

        first_date = first_result.date() if isinstance(first_result, datetime) else first_result
        today = datetime.now(UTC).date()

        return first_date, today


def delete_user_training_load(user_id: str) -> int:
    """Delete all DailyTrainingLoad records for a user.

    Args:
        user_id: User ID

    Returns:
        Number of records deleted
    """
    with get_session() as session:
        result = session.execute(delete(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id))
        deleted_count = result.rowcount
        session.commit()
        logger.debug(f"Deleted {deleted_count} DailyTrainingLoad records for user {user_id}")
        return deleted_count


def recompute_user_training_load(user_id: str, start_date: date, end_date: date) -> dict[str, int]:
    """Recompute training load metrics for a user from scratch.

    Args:
        user_id: User ID
        start_date: First activity date
        end_date: Today's date

    Returns:
        Dictionary with counts of records created
    """
    logger.info(f"Recomputing training load for user {user_id} from {start_date.isoformat()} to {end_date.isoformat()}")

    with get_session() as session:
        # Fetch all activities in date range
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_time >= datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC),
                Activity.start_time <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC),
            )
            .order_by(Activity.start_time)
        ).all()

        activity_list = [a[0] for a in activities]
        logger.info(f"Found {len(activity_list)} activities for user {user_id}")

        if not activity_list:
            logger.warning(f"No activities found for user {user_id} in date range")
            return {"daily_created": 0, "daily_updated": 0}

        # Use default athlete thresholds (athlete-specific calibration can be added later)
        athlete_thresholds = AthleteThresholds()

        # Compute daily TSS loads (unified metric from spec)
        daily_tss_loads = compute_daily_tss_load(activity_list, start_date, end_date, athlete_thresholds)

        # Compute CTL, ATL, Form (FSB) from TSS
        metrics = compute_ctl_atl_form_from_tss(daily_tss_loads, start_date, end_date)

        # Store results in daily_training_load table
        daily_created = 0

        for date_val, tss_load in daily_tss_loads.items():
            metrics_for_date = metrics.get(date_val, {"ctl": 0.0, "atl": 0.0, "fsb": 0.0})

            # Note: TSB column stores Form (FSB) value for backward compatibility
            form_value = metrics_for_date.get("fsb", 0.0)

            # Create new record (we deleted all existing ones, so this is always a new record)
            daily_load = DailyTrainingLoad(
                user_id=user_id,
                date=datetime.combine(date_val, datetime.min.time()).replace(tzinfo=UTC),
                ctl=metrics_for_date["ctl"],
                atl=metrics_for_date["atl"],
                tsb=form_value,  # Storing Form (FSB) in TSB column for backward compatibility
                load_score=tss_load,  # Daily TSS load
            )
            session.add(daily_load)
            daily_created += 1

        # Commit all changes
        session.commit()

        logger.info(
            f"Recomputation complete for user {user_id}: "
            f"daily_created={daily_created}, date_range={start_date.isoformat()} to {end_date.isoformat()}"
        )

        return {
            "daily_created": daily_created,
            "daily_updated": 0,  # Always 0 since we delete first
        }


def validate_user_metrics(user_id: str) -> dict[str, bool | float]:
    """Validate recomputed metrics for a user.

    Args:
        user_id: User ID

    Returns:
        Dictionary with validation results
    """
    with get_session() as session:
        # Get recent metrics
        recent_records = session.execute(
            select(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id).order_by(DailyTrainingLoad.date.desc()).limit(14)
        ).all()

        if not recent_records:
            return {"has_data": False, "record_count": 0}

        records = [r[0] for r in recent_records]
        ctl_values = [r.ctl for r in records]
        atl_values = [r.atl for r in records]
        tsb_values = [r.tsb for r in records]

        # Validation checks
        ctl_not_symmetric = not all(abs(c) > 90 for c in ctl_values)  # Not stuck near ±100
        atl_not_symmetric = not all(abs(a) > 90 for a in atl_values)  # Not stuck near ±100
        ctl_atl_different = any(abs(c - a) > 1.0 for c, a in zip(ctl_values, atl_values, strict=False))  # Not identical

        # Check TSB range (should vary, not be constant)
        tsb_range = max(tsb_values) - min(tsb_values) if tsb_values else 0.0
        tsb_varies = tsb_range > 1.0

        return {
            "has_data": True,
            "record_count": len(records),
            "ctl_not_symmetric": ctl_not_symmetric,
            "atl_not_symmetric": atl_not_symmetric,
            "ctl_atl_different": ctl_atl_different,
            "tsb_varies": tsb_varies,
            "tsb_range": tsb_range,
            "recent_ctl": ctl_values[0] if ctl_values else 0.0,
            "recent_atl": atl_values[0] if atl_values else 0.0,
            "recent_tsb": tsb_values[0] if tsb_values else 0.0,
        }


def recalculate_all_training_load_metrics(dry_run: bool = False) -> dict[str, int | dict]:
    """Recalculate training load metrics for all users.

    Args:
        dry_run: If True, don't make changes, just report what would be done

    Returns:
        Dictionary with summary statistics
    """
    logger.info("=" * 80)
    logger.info("TRAINING LOAD METRICS RECALCULATION")
    logger.info("=" * 80)
    if dry_run:
        logger.warning("DRY RUN MODE - No changes will be made")

    user_ids = get_all_user_ids()

    if not user_ids:
        logger.warning("No users found with activities")
        return {"users_processed": 0, "total_deleted": 0, "total_created": 0}

    total_deleted = 0
    total_created = 0
    users_processed = 0
    users_failed = 0
    validation_results: dict[str, dict[str, bool | float]] = {}

    for idx, user_id in enumerate(user_ids, 1):
        logger.info(f"\n[{idx}/{len(user_ids)}] Processing user {user_id}")

        try:
            # Get date range
            first_date, today = get_user_activity_date_range(user_id)

            if first_date is None:
                logger.warning(f"No activities found for user {user_id}, skipping")
                continue

            logger.info(f"Date range: {first_date.isoformat()} to {today.isoformat()}")

            if not dry_run:
                # Delete existing records
                deleted = delete_user_training_load(user_id)
                total_deleted += deleted

                # Recompute from scratch
                result = recompute_user_training_load(user_id, first_date, today)
                total_created += result["daily_created"]
                users_processed += 1

                # Validate (sample first 2 users)
                if users_processed <= 2:
                    validation = validate_user_metrics(user_id)
                    validation_results[user_id] = validation
                    logger.info(f"Validation for {user_id}: {validation}")
            else:
                # Dry run: just count what would be done
                logger.info(f"[DRY RUN] Would delete and recompute for user {user_id}")
                users_processed += 1

        except Exception as e:
            logger.error(f"Error processing user {user_id}: {e}", exc_info=True)
            users_failed += 1

    summary = {
        "users_processed": users_processed,
        "users_failed": users_failed,
        "total_deleted": total_deleted,
        "total_created": total_created,
        "validation_results": validation_results,
    }

    logger.info("\n" + "=" * 80)
    logger.info("RECALCULATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Users processed: {users_processed}")
    logger.info(f"Users failed: {users_failed}")
    logger.info(f"Total records deleted: {total_deleted}")
    logger.info(f"Total records created: {total_created}")
    logger.info(f"Validation results: {len(validation_results)} users validated")

    if validation_results:
        logger.info("\nValidation Details:")
        for user_id, validation in validation_results.items():
            logger.info(f"  User {user_id}:")
            logger.info(f"    Records: {validation.get('record_count', 0)}")
            logger.info(f"    CTL not symmetric: {validation.get('ctl_not_symmetric', False)}")
            logger.info(f"    ATL not symmetric: {validation.get('atl_not_symmetric', False)}")
            logger.info(f"    CTL/ATL different: {validation.get('ctl_atl_different', False)}")
            logger.info(f"    TSB varies: {validation.get('tsb_varies', False)}")
            logger.info(f"    Recent CTL: {validation.get('recent_ctl', 0.0):.2f}")
            logger.info(f"    Recent ATL: {validation.get('recent_atl', 0.0):.2f}")
            logger.info(f"    Recent TSB: {validation.get('recent_tsb', 0.0):.2f}")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Recalculate training load metrics for all users")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode: don't make changes, just report what would be done",
    )
    args = parser.parse_args()

    try:
        result = recalculate_all_training_load_metrics(dry_run=args.dry_run)
        if args.dry_run:
            logger.info("\nDry run completed. Run without --dry-run to apply changes.")
        else:
            logger.info("\nRecalculation completed successfully!")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Recalculation failed: {e}", exc_info=True)
        sys.exit(1)
