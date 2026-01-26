"""Trigger metrics recomputation for a user.

This script triggers metrics recomputation to fill in missing data points.
It will create/update metrics for the last 50 days (including today).

Usage:
    python scripts/trigger_metrics_recompute.py [user_id]

If no user_id is provided, it will prompt for it.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from app.metrics.computation_service import recompute_metrics_for_user


def main() -> None:
    """Main function to trigger metrics recomputation."""
    # Get user_id from command line or prompt
    if len(sys.argv) > 1:
        user_id = sys.argv[1]
    else:
        user_id = input("Enter user_id: ").strip()
        if not user_id:
            logger.error("user_id is required")
            sys.exit(1)

    logger.info(f"Triggering metrics recomputation for user_id={user_id}")

    # Recompute last 50 days (CTL window + buffer) to ensure we get all recent days
    since_date = datetime.now(tz=timezone.utc).date() - timedelta(days=50)

    try:
        result = recompute_metrics_for_user(user_id, since_date=since_date)
        logger.info(f"Recomputation complete: {result}")
        print(f"\n✅ Metrics recomputation complete!")
        print(f"   - Created: {result.get('daily_created', 0)} days")
        print(f"   - Updated: {result.get('daily_updated', 0)} days")
        print(f"   - Skipped: {result.get('daily_skipped', 0)} days (historical)")
        print(f"   - Weekly created: {result.get('weekly_created', 0)}")
        print(f"   - Weekly updated: {result.get('weekly_updated', 0)}")
    except Exception as e:
        logger.exception(f"Failed to recompute metrics: {e}")
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
