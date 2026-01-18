"""Create sample training data for a test user.

This script generates sample DailyTrainingLoad records for testing purposes.
Useful when you need training data for a test user but don't have activities synced yet.
"""

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import DailyTrainingLoad, StravaAccount
from app.db.session import get_session
from app.metrics.load_computation import (
    AthleteThresholds,
    compute_ctl_atl_form_from_tss,
    compute_daily_tss_load,
)
from app.state.api_helpers import get_user_id_from_athlete_id


def generate_sample_training_data(
    user_id: str | None = None,
    athlete_id: int | None = None,
    days: int = 60,
    activity_days: int = 30,
) -> dict[str, int]:
    """Generate sample training data for a user.

    Creates sample DailyTrainingLoad records with realistic CTL/ATL/TSB values
    by simulating activities over the past N days.

    Args:
        user_id: User ID (if None, will look up from athlete_id)
        athlete_id: Athlete ID (required if user_id is None)
        days: Number of days of training data to generate (default: 60)
        activity_days: Number of days with actual activities (default: 30)

    Returns:
        Dictionary with counts of records created

    Raises:
        ValueError: If neither user_id nor athlete_id is provided
    """
    # Resolve user_id if not provided
    if user_id is None:
        if athlete_id is None:
            raise ValueError("Either user_id or athlete_id must be provided")
        user_id = get_user_id_from_athlete_id(athlete_id)
        if user_id is None:
            raise ValueError(f"No user_id found for athlete_id={athlete_id}")

    logger.info(f"Generating sample training data for user_id={user_id}, days={days}")

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=days)

    # Generate realistic sample daily TSS loads
    # Simulate a training pattern: higher load on some days, rest days, etc.
    daily_tss_loads: dict[date, float] = {}

    current_date = start_date
    day_count = 0
    while current_date <= end_date:
        # Create a realistic training pattern
        # Higher load early in the period, tapering toward the end
        progress = day_count / days  # 0.0 to 1.0

        # Base load varies by day of week (weekends typically higher)
        is_weekend = current_date.weekday() >= 5
        base_multiplier = 1.5 if is_weekend else 1.0

        # Taper: start high, reduce over time
        taper_factor = max(0.3, 1.0 - progress * 0.5)

        # Only create activities on some days (simulate training schedule)
        if day_count < activity_days:
            # Realistic TSS range: 20-120 for most days
            base_tss = 50.0 * base_multiplier * taper_factor
            # Add some variation
            variation = (day_count % 7) * 10.0  # Vary by day of week cycle
            daily_tss = max(0.0, base_tss + variation)
        else:
            # Rest days for the remaining period
            daily_tss = 0.0

        daily_tss_loads[current_date] = daily_tss
        current_date += timedelta(days=1)
        day_count += 1

    # Compute CTL, ATL, TSB from TSS loads
    metrics = compute_ctl_atl_form_from_tss(daily_tss_loads, start_date, end_date)

    # Store results in daily_training_load table
    created_count = 0

    with get_session() as session:
        # Delete existing records for this user (if any)
        existing = session.execute(
            select(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id)
        ).all()
        if existing:
            logger.info(f"Deleting {len(existing)} existing DailyTrainingLoad records for user {user_id}")
            for record in existing:
                session.delete(record[0])
            session.flush()

        # Create new records
        for date_val in sorted(daily_tss_loads.keys()):
            metrics_for_date = metrics.get(date_val, {"ctl": 0.0, "atl": 0.0, "fsb": 0.0})

            daily_load = DailyTrainingLoad(
                user_id=user_id,
                day=date_val,
                ctl=metrics_for_date["ctl"],
                atl=metrics_for_date["atl"],
                tsb=metrics_for_date["fsb"],  # TSB stores Form (FSB)
            )
            session.add(daily_load)
            created_count += 1

        session.commit()

    logger.info(
        f"Created {created_count} sample DailyTrainingLoad records for user {user_id} "
        f"(date range: {start_date.isoformat()} to {end_date.isoformat()})"
    )

    # Log recent metrics
    with get_session() as session:
        recent = session.execute(
            select(DailyTrainingLoad)
            .where(DailyTrainingLoad.user_id == user_id)
            .order_by(DailyTrainingLoad.day.desc())
            .limit(7)
        ).all()

        if recent:
            logger.info("Recent metrics (last 7 days):")
            for record in reversed(recent):
                dtl = record[0]
                logger.info(
                    f"  {dtl.day.isoformat()}: CTL={dtl.ctl:.1f}, ATL={dtl.atl:.1f}, TSB={dtl.tsb:.1f}"
                )

    return {"created": created_count}


def main() -> None:
    """Main entry point for script."""
    parser = argparse.ArgumentParser(description="Create sample training data for a test user")
    parser.add_argument("--user-id", type=str, help="User ID (UUID)")
    parser.add_argument("--athlete-id", type=int, help="Athlete ID (will look up user_id if not provided)")
    parser.add_argument("--days", type=int, default=60, help="Number of days of training data to generate (default: 60)")
    parser.add_argument("--activity-days", type=int, default=30, help="Number of days with actual activities (default: 30)")

    args = parser.parse_args()

    try:
        result = generate_sample_training_data(
            user_id=args.user_id,
            athlete_id=args.athlete_id,
            days=args.days,
            activity_days=args.activity_days,
        )
        print(f"✅ Created {result['created']} sample training data records")
    except Exception as e:
        logger.exception(f"❌ Error creating sample training data: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
