"""Recalculate activity-level TSS for all activities.

This script:
1. Iterates through all activities with duration_seconds
2. Recomputes TSS using compute_activity_tss
3. Updates activity.tss and activity.tss_version if TSS changed significantly
4. Supports dry-run mode for validation before applying changes
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import Activity, UserSettings
from app.db.session import get_session
from app.metrics.load_computation import AthleteThresholds, compute_activity_tss

BATCH_SIZE = 500


def run(dry_run: bool = True) -> dict[str, int]:
    """Recalculate TSS for all activities.

    Args:
        dry_run: If True, don't make changes, just report what would be done

    Returns:
        Dictionary with summary statistics
    """
    logger.info("=" * 80)
    logger.info("ACTIVITY TSS RECALCULATION")
    logger.info("=" * 80)
    if dry_run:
        logger.warning("DRY RUN MODE - No changes will be made")

    total_processed = 0
    total_updated = 0
    total_skipped = 0
    total_unchanged = 0

    with get_session() as db:
        query = db.query(Activity).filter(Activity.duration_seconds.isnot(None)).order_by(Activity.start_time.asc())

        for i, activity in enumerate(query.yield_per(BATCH_SIZE), 1):
            total_processed += 1

            try:
                # Get user settings for threshold configuration
                user_settings = db.query(UserSettings).filter_by(user_id=activity.user_id).first()
                athlete_thresholds = _build_athlete_thresholds(user_settings)
                
                # Compute TSS with user-specific thresholds
                new_tss = compute_activity_tss(activity, athlete_thresholds)

                if new_tss is None:
                    total_skipped += 1
                    continue

                old_tss = activity.tss or 0.0
                tss_diff = abs(old_tss - new_tss)

                # Only update if difference is significant (>= 0.5)
                if tss_diff < 0.5:
                    total_unchanged += 1
                    continue

                logger.info(f"[{i}] Activity {activity.id}: TSS {old_tss:.2f} → {new_tss:.2f} (diff: {tss_diff:.2f})")

                if not dry_run:
                    activity.tss = new_tss
                    activity.tss_version = "v2"
                    total_updated += 1

            except Exception as e:
                logger.error(f"Error processing activity {activity.id}: {e}", exc_info=True)
                total_skipped += 1

        if not dry_run:
            db.commit()
            logger.info(f"Committed {total_updated} TSS updates to database")

    summary = {
        "total_processed": total_processed,
        "total_updated": total_updated,
        "total_unchanged": total_unchanged,
        "total_skipped": total_skipped,
    }

    logger.info("\n" + "=" * 80)
    logger.info("TSS RECALCULATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total processed: {total_processed}")
    logger.info(f"Total updated: {total_updated}")
    logger.info(f"Total unchanged: {total_unchanged}")
    logger.info(f"Total skipped: {total_skipped}")

    return summary


def _build_athlete_thresholds(user_settings: UserSettings | None) -> AthleteThresholds | None:
    """Build AthleteThresholds from UserSettings.

    Args:
        user_settings: User settings with threshold configuration

    Returns:
        AthleteThresholds instance or None if no user settings
    """
    if not user_settings:
        return None

    return AthleteThresholds(
        ftp_watts=user_settings.ftp_watts,
        threshold_pace_ms=user_settings.threshold_pace_ms,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Recalculate activity-level TSS for all activities")
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
        result = run(dry_run=dry_run)
        if dry_run:
            logger.info("\nDry run completed. Run with --apply to apply changes.")
        else:
            logger.info("\nTSS recalculation completed successfully!")
        sys.exit(0)
    except Exception as e:
        logger.error(f"TSS recalculation failed: {e}", exc_info=True)
        sys.exit(1)
