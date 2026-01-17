"""Fix training load metrics for a specific user.

This script deletes all DailyTrainingLoad records for a user and recomputes them
correctly from scratch, ensuring EWMA integrity (sequential computation from first activity).
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


def fix_user_training_load(user_id: str) -> dict[str, int | str]:
    """Delete and recompute training load metrics for a user from scratch.

    This ensures EWMA integrity by computing sequentially from the first activity
    to today, without any historical overwrites.

    Args:
        user_id: User ID to fix

    Returns:
        Dictionary with counts and status
    """
    logger.info(f"Fixing training load for user {user_id}")

    with get_session() as session:
        # Get date range from activities
        first_result = session.execute(
            select(func.min(Activity.starts_at)).where(Activity.user_id == user_id)
        ).scalar()

        if not first_result:
            logger.warning(f"No activities found for user {user_id}")
            return {"status": "error", "message": "No activities found", "deleted": 0, "created": 0}

        first_date = first_result.date() if isinstance(first_result, datetime) else first_result
        end_date = datetime.now(UTC).date()

        logger.info(f"Date range: {first_date.isoformat()} to {end_date.isoformat()}")

        # Delete all existing records for this user
        result = session.execute(delete(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id))
        deleted_count = result.rowcount
        session.commit()
        logger.info(f"Deleted {deleted_count} existing DailyTrainingLoad records for user {user_id}")

        # Fetch all activities in date range
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.starts_at >= datetime.combine(first_date, datetime.min.time()).replace(tzinfo=UTC),
                Activity.starts_at <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC),
            )
            .order_by(Activity.starts_at)
        ).all()

        activity_list = [a[0] for a in activities]
        logger.info(f"Found {len(activity_list)} activities for user {user_id}")

        if not activity_list:
            logger.warning(f"No activities found for user {user_id} in date range")
            return {"status": "error", "message": "No activities in date range", "deleted": deleted_count, "created": 0}

        # Use default athlete thresholds
        athlete_thresholds = AthleteThresholds()

        # Compute daily TSS loads (aggregate activities → daily TSS)
        daily_tss_loads = compute_daily_tss_load(activity_list, first_date, end_date, athlete_thresholds)

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
                day=date_val,
                ctl=ctl_val,
                atl=atl_val,
                tsb=form_value,  # Storing Form (FSB) in TSB column for backward compatibility
            )
            session.add(daily_load)
            daily_created += 1

        # Commit once at the end
        session.commit()

        logger.info(
            f"Recomputation complete for user {user_id}: "
            f"deleted={deleted_count}, created={daily_created}, "
            f"date_range={first_date.isoformat()} to {end_date.isoformat()}"
        )

        return {
            "status": "success",
            "deleted": deleted_count,
            "created": daily_created,
            "date_range": f"{first_date.isoformat()} to {end_date.isoformat()}",
        }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.error("Usage: python scripts/fix_user_training_load.py <user_id>")
        sys.exit(1)

    user_id = sys.argv[1]
    logger.info(f"Fixing training load for user: {user_id}")

    try:
        result = fix_user_training_load(user_id)
        if result["status"] == "success":
            logger.info("\n✅ Success!")
            logger.info(f"  Deleted: {result['deleted']} records")
            logger.info(f"  Created: {result['created']} records")
            logger.info(f"  Date range: {result['date_range']}")
        else:
            logger.error(f"\n❌ Error: {result.get('message', 'Unknown error')}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to fix training load for user {user_id}: {e}", exc_info=True)
        sys.exit(1)
