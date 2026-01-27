"""Script to manually trigger Garmin backfill for a user.

Usage:
    python scripts/backfill_garmin.py <user_id> [--force] [--days N]

Examples:
    # Backfill last 90 days (default)
    python scripts/backfill_garmin.py user_123456

    # Force backfill even if recently synced
    python scripts/backfill_garmin.py user_123456 --force

    # Backfill last 30 days
    python scripts/backfill_garmin.py user_123456 --days 30
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import backfill_garmin_activities


def main() -> int:
    """Run Garmin backfill for a user."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    user_id = sys.argv[1]
    force = "--force" in sys.argv
    days = None

    # Parse --days argument
    if "--days" in sys.argv:
        try:
            days_idx = sys.argv.index("--days")
            days = int(sys.argv[days_idx + 1])
        except (IndexError, ValueError):
            print("Error: --days requires a number")
            return 1

    logger.info(f"Starting Garmin backfill for user_id={user_id}, force={force}, days={days}")

    # Verify user has Garmin integration
    with get_session() as session:
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
            )
        ).first()

        if not integration:
            logger.error(f"No active Garmin integration found for user_id={user_id}")
            print(f"Error: No active Garmin integration found for user_id={user_id}")
            return 1

        logger.info(f"Found Garmin integration for user_id={user_id}")

    # Calculate date range if days specified
    from_date = None
    to_date = None
    if days:
        to_date = datetime.now(timezone.utc)
        from_date = to_date - timedelta(days=days)
        logger.info(f"Backfill window: {from_date.date()} to {to_date.date()} ({days} days)")

    # Run backfill
    try:
        result = backfill_garmin_activities(
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            force=force,
        )

        print("\n" + "=" * 60)
        print("Garmin Backfill Results")
        print("=" * 60)
        print(f"Status: {result.get('status', 'unknown')}")
        print(f"Imported: {result.get('ingested_count', 0)}")
        print(f"Skipped: {result.get('skipped_count', 0)}")
        print(f"  - Duplicates: {result.get('duplicate_count', 0)}")
        print(f"  - Strava duplicates: {result.get('strava_duplicate_count', 0)}")
        print(f"Errors: {result.get('error_count', 0)}")
        print(f"Total fetched: {result.get('total_fetched', 0)}")
        print("=" * 60)

        if result.get("error_count", 0) > 0:
            logger.warning(f"Backfill completed with {result.get('error_count')} errors")
            return 1

        return 0

    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
