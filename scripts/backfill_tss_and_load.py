"""Backfill TSS and daily training load for all users.

This script:
1. Recalculates TSS for activities that have NULL or 0 TSS (but have valid activity data)
2. Recalculates daily training load metrics (CTL, ATL, TSB) from activities
3. Supports dry-run mode for validation before applying changes

This addresses the issue where TSS and load data are zero or missing.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import delete, func, or_, select

from app.db.models import Activity, AthleteProfile, DailyTrainingLoad, UserSettings
from app.db.session import get_session
from app.metrics.load_computation import (
    AthleteThresholds,
    compute_activity_tss,
    compute_ctl_atl_form_from_tss,
    compute_daily_tss_load,
)
from app.workouts.models import Workout  # Import to ensure foreign key relationship is known

BATCH_SIZE = 500


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


def backfill_activity_tss_for_user(user_id: str, dry_run: bool = True) -> dict[str, int]:
    """Backfill TSS for activities with NULL or 0 TSS.

    Args:
        user_id: User ID
        dry_run: If True, don't make changes, just report what would be done

    Returns:
        Dictionary with counts of activities processed/updated
    """
    logger.info(f"Backfilling TSS for user {user_id}")

    with get_session() as session:
        # Get athlete profile and user settings for threshold configuration
        athlete_profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
        user_settings = session.query(UserSettings).filter_by(user_id=user_id).first()
        athlete_thresholds = _build_athlete_thresholds(athlete_profile, user_settings)

        # Find activities with NULL or 0 TSS but with valid duration
        activities_query = (
            session.query(Activity)
            .filter(
                Activity.user_id == user_id,
                Activity.duration_seconds.isnot(None),
                Activity.duration_seconds > 0,
                or_(Activity.tss.is_(None), Activity.tss == 0.0),
            )
            .order_by(Activity.starts_at)
        )

        total_processed = 0
        total_updated = 0
        total_would_update = 0
        total_unchanged = 0
        total_skipped = 0

        for activity in activities_query.yield_per(BATCH_SIZE):
            total_processed += 1

            try:
                # Compute TSS
                new_tss = compute_activity_tss(activity, athlete_thresholds)

                if new_tss is None or new_tss == 0.0:
                    # Skip if still 0 (might be a short/invalid activity)
                    total_skipped += 1
                    continue

                old_tss = activity.tss or 0.0
                tss_diff = abs(old_tss - new_tss)

                if tss_diff < 0.01:  # Very small difference, skip
                    total_unchanged += 1
                    continue

                activity_id_str = str(activity.id)
                logger.info(
                    f"  Activity {activity_id_str[:8]}... {activity.starts_at.date()}: "
                    f"TSS {old_tss:.2f} → {new_tss:.2f} (diff: {tss_diff:.2f})"
                )

                if not dry_run:
                    activity.tss = new_tss
                    activity.tss_version = "v2"
                    total_updated += 1
                else:
                    total_would_update += 1

            except Exception as e:
                logger.error(f"Error processing activity {activity.id}: {e}", exc_info=True)
                total_skipped += 1

        if not dry_run and total_updated > 0:
            session.commit()
            logger.info(f"  Committed {total_updated} TSS updates for user {user_id}")

        return {
            "total_processed": total_processed,
            "total_updated": total_updated,
            "total_would_update": total_would_update,
            "total_unchanged": total_unchanged,
            "total_skipped": total_skipped,
        }


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
        first_result = session.execute(select(func.min(Activity.starts_at)).where(Activity.user_id == user_id)).scalar()

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
        logger.debug(f"  Deleted {deleted_count} DailyTrainingLoad records for user {user_id}")
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
    logger.info(f"  Recomputing training load from {start_date.isoformat()} to {end_date.isoformat()}")

    with get_session() as session:
        # Fetch all activities in date range
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.starts_at >= datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC),
                Activity.starts_at <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC),
            )
            .order_by(Activity.starts_at)
        ).all()

        activity_list = [a[0] for a in activities]
        logger.info(f"  Found {len(activity_list)} activities for user {user_id}")

        if not activity_list:
            logger.warning(f"  No activities found for user {user_id} in date range")
            return {"daily_created": 0, "daily_updated": 0}

        # Get athlete profile and user settings for threshold configuration
        athlete_profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
        user_settings = session.query(UserSettings).filter_by(user_id=user_id).first()
        athlete_thresholds = _build_athlete_thresholds(athlete_profile, user_settings)

        # Compute daily TSS loads (unified metric from spec)
        daily_tss_loads = compute_daily_tss_load(activity_list, start_date, end_date, athlete_thresholds)

        # Compute CTL, ATL, Form (FSB) from TSS
        metrics = compute_ctl_atl_form_from_tss(daily_tss_loads, start_date, end_date)

        # Store results in daily_training_load table
        daily_created = 0

        for date_val in daily_tss_loads:
            metrics_for_date = metrics.get(date_val, {"ctl": 0.0, "atl": 0.0, "fsb": 0.0})

            # Note: TSB column stores Form (FSB) value for backward compatibility
            form_value = metrics_for_date.get("fsb", 0.0)

            # Create new record (we deleted all existing ones, so this is always a new record)
            daily_load = DailyTrainingLoad(
                user_id=user_id,
                day=date_val,
                ctl=metrics_for_date["ctl"],
                atl=metrics_for_date["atl"],
                tsb=form_value,  # Storing Form (FSB) in TSB column for backward compatibility
            )
            session.add(daily_load)
            daily_created += 1

        # Commit all changes
        session.commit()

        logger.info(
            f"  Recomputation complete: daily_created={daily_created}, "
            f"date_range={start_date.isoformat()} to {end_date.isoformat()}"
        )

        return {
            "daily_created": daily_created,
            "daily_updated": 0,  # Always 0 since we delete first
        }


def _build_athlete_thresholds(
    athlete_profile: AthleteProfile | None,
    user_settings: UserSettings | None,
) -> AthleteThresholds | None:
    """Build AthleteThresholds from AthleteProfile and UserSettings.

    Args:
        athlete_profile: Athlete profile with threshold configuration (preferred)
        user_settings: User settings with threshold configuration (fallback)

    Returns:
        AthleteThresholds instance or None if no thresholds found
    """
    # Prefer athlete_profile over user_settings (athlete_profile has ftp_watts, threshold_pace_sec_per_km)
    ftp_watts = None
    threshold_pace_ms = None

    if athlete_profile:
        ftp_watts = athlete_profile.ftp_watts
        # Convert threshold_pace_sec_per_km to threshold_pace_ms (meters per second)
        if athlete_profile.threshold_pace_sec_per_km is not None:
            # Convert sec/km to m/s: 1000m / sec = m/s
            threshold_pace_ms = 1000.0 / float(athlete_profile.threshold_pace_sec_per_km)

    # Fallback to user_settings if not found in athlete_profile
    if (ftp_watts is None or threshold_pace_ms is None) and user_settings:
        # Try getattr for fields that might exist in DB but not in model
        user_ftp = getattr(user_settings, "ftp_watts", None)
        user_pace_ms = getattr(user_settings, "threshold_pace_ms", None)

        # If not found as attributes, try checking preferences JSONB field
        if user_ftp is None and hasattr(user_settings, "preferences"):
            preferences = user_settings.preferences or {}
            user_ftp = preferences.get("ftp_watts")
            user_pace_ms = preferences.get("threshold_pace_ms")

        if ftp_watts is None and user_ftp is not None:
            ftp_watts = user_ftp
        if threshold_pace_ms is None and user_pace_ms is not None:
            threshold_pace_ms = user_pace_ms

    # Return None if no thresholds found at all
    if ftp_watts is None and threshold_pace_ms is None:
        return None

    return AthleteThresholds(
        ftp_watts=ftp_watts,
        threshold_pace_ms=threshold_pace_ms,
    )


def backfill_tss_and_load(dry_run: bool = True) -> dict[str, int]:
    """Backfill TSS and daily training load for all users.

    Args:
        dry_run: If True, don't make changes, just report what would be done

    Returns:
        Dictionary with summary statistics
    """
    logger.info("=" * 80)
    logger.info("TSS AND LOAD BACKFILL")
    logger.info("=" * 80)
    if dry_run:
        logger.warning("DRY RUN MODE - No changes will be made")

    user_ids = get_all_user_ids()

    if not user_ids:
        logger.warning("No users found with activities")
        return {"users_processed": 0, "total_tss_updated": 0, "total_load_records_created": 0}

    total_tss_updated = 0
    total_tss_would_update = 0
    total_load_records_created = 0
    total_load_records_deleted = 0
    users_processed = 0
    users_failed = 0

    for idx, user_id in enumerate(user_ids, 1):
        logger.info(f"\n[{idx}/{len(user_ids)}] Processing user {user_id}")

        try:
            # Step 1: Backfill TSS for activities
            tss_result = backfill_activity_tss_for_user(user_id, dry_run=dry_run)
            if dry_run:
                total_tss_would_update += tss_result["total_would_update"]
            else:
                total_tss_updated += tss_result["total_updated"]

            # Step 2: Recompute daily training load from activities
            first_date, today = get_user_activity_date_range(user_id)

            if first_date is None:
                logger.warning(f"  No activities found for user {user_id}, skipping load recomputation")
                continue

            if not dry_run:
                # Delete existing records
                deleted = delete_user_training_load(user_id)
                total_load_records_deleted += deleted

                # Recompute from scratch
                load_result = recompute_user_training_load(user_id, first_date, today)
                total_load_records_created += load_result["daily_created"]
            else:
                # Dry run: just count what would be done
                logger.info(f"  [DRY RUN] Would delete and recompute daily training load for user {user_id}")

            users_processed += 1

        except Exception as e:
            logger.error(f"Error processing user {user_id}: {e}", exc_info=True)
            users_failed += 1

    summary = {
        "users_processed": users_processed,
        "users_failed": users_failed,
        "total_tss_updated": total_tss_updated,
        "total_tss_would_update": total_tss_would_update,
        "total_load_records_deleted": total_load_records_deleted,
        "total_load_records_created": total_load_records_created,
    }

    logger.info("\n" + "=" * 80)
    logger.info("BACKFILL SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Users processed: {users_processed}")
    logger.info(f"Users failed: {users_failed}")
    if dry_run:
        logger.info(f"TSS would update: {total_tss_would_update} activities")
        logger.info(f"Daily training load would be recomputed for {users_processed} users")
    else:
        logger.info(f"TSS updated: {total_tss_updated} activities")
        logger.info(f"Daily training load records deleted: {total_load_records_deleted}")
        logger.info(f"Daily training load records created: {total_load_records_created}")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill TSS and daily training load for all users")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode: don't make changes, just report what would be done (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (overrides --dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    try:
        result = backfill_tss_and_load(dry_run=dry_run)
        if dry_run:
            logger.info("\nDry run completed. Run with --apply to apply changes.")
        else:
            logger.info("\nBackfill completed successfully!")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        sys.exit(1)
