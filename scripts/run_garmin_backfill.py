#!/usr/bin/env python3
"""Standalone script to run Garmin backfill on Render shell.

Usage on Render:
    python scripts/run_garmin_backfill.py <user_id> [--force] [--days N]

Examples:
    python scripts/run_garmin_backfill.py user_123456
    python scripts/run_garmin_backfill.py user_123456 --force
    python scripts/run_garmin_backfill.py user_123456 --days 30
"""

import os
import sys
from datetime import UTC, datetime, timedelta, timezone
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
    """Run Garmin backfill."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nError: user_id is required")
        print("Usage: python scripts/run_garmin_backfill.py <user_id> [--force] [--days N]")
        return 1

    user_id = sys.argv[1]
    force = "--force" in sys.argv or "-f" in sys.argv
    days = None

    # Parse --days argument
    if "--days" in sys.argv or "-d" in sys.argv:
        try:
            flag = "--days" if "--days" in sys.argv else "-d"
            days_idx = sys.argv.index(flag)
            days = int(sys.argv[days_idx + 1])
        except (IndexError, ValueError):
            print("Error: --days requires a number")
            return 1

    print("=" * 70)
    print(f"Garmin Backfill for user_id: {user_id}")
    print(f"Force: {force}, Days: {days or 'default (90)'}")
    print("=" * 70)

    # Verify user has Garmin integration
    try:
        with get_session() as session:
            integration = session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == user_id,
                    UserIntegration.provider == "garmin",
                    UserIntegration.revoked_at.is_(None),
                )
            ).first()

            if not integration:
                print(f"\n❌ ERROR: No active Garmin integration found for user_id={user_id}")
                return 1

            print(f"✓ Found Garmin integration for user_id={user_id}")
    except Exception as e:
        print(f"\n❌ ERROR checking integration: {e}")
        logger.exception("Failed to check Garmin integration")
        return 1

    # Calculate date range if days specified
    from_date = None
    to_date = None
    if days:
        to_date = datetime.now(UTC)
        from_date = to_date - timedelta(days=days)
        print(f"Backfill window: {from_date.date()} to {to_date.date()} ({days} days)")

    # Run backfill
    print("\n🚀 Starting backfill...")
    try:
        result = backfill_garmin_activities(
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            force=force,
        )

        print("\n" + "=" * 70)
        print("BACKFILL RESULTS")
        print("=" * 70)
        print(f"Status: {result.get('status', 'unknown')}")
        print(f"Imported: {result.get('ingested_count', 0)}")
        print(f"Skipped: {result.get('skipped_count', 0)}")
        print(f"  - Duplicates: {result.get('duplicate_count', 0)}")
        print(f"  - Strava duplicates: {result.get('strava_duplicate_count', 0)}")
        print(f"Errors: {result.get('error_count', 0)}")
        print(f"Total fetched: {result.get('total_fetched', 0)}")
        print("=" * 70)

        if int(result.get("error_count", 0)) > 0:
            print(f"\n⚠️  WARNING: Backfill completed with {result.get('error_count')} errors")
            print("Check logs for details")
            return 1
    except Exception as e:
        print(f"\n❌ ERROR: Backfill failed: {e}")
        logger.exception("Garmin backfill failed")
        return 1
    else:
        print("\n✅ SUCCESS: Backfill completed successfully!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
