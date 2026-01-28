#!/usr/bin/env python3
"""Backfill Garmin activities for ALL users with Garmin integrations.

This script will:
1. Find all users with active Garmin integrations
2. Run backfill for each user (full historical)
3. Continue even if some users fail
4. Show summary at the end

Usage on Render:
    python scripts/backfill_garmin_all_users.py [--force] [--days N]

Examples:
    python scripts/backfill_garmin_all_users.py
    python scripts/backfill_garmin_all_users.py --force
    python scripts/backfill_garmin_all_users.py --days 365
"""

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
    """Backfill Garmin activities for all users."""
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
    print("GARMIN BACKFILL - ALL USERS")
    print("=" * 70)
    print(f"Force: {force}, Days: {days or 'default (90)'}")
    print("=" * 70)

    # Find all users with Garmin integrations
    try:
        with get_session() as session:
            integrations = session.execute(
                select(UserIntegration).where(
                    UserIntegration.provider == "garmin",
                    UserIntegration.revoked_at.is_(None),
                )
            ).all()

            if not integrations:
                print("\n❌ No active Garmin integrations found")
                return 1

            user_ids = [integration[0].user_id for integration in integrations]
            print(f"\n✓ Found {len(user_ids)} user(s) with Garmin integrations")
            print(f"Users: {', '.join(user_ids[:5])}{'...' if len(user_ids) > 5 else ''}\n")

    except Exception as e:
        print(f"\n❌ ERROR finding users: {e}")
        logger.exception("Failed to find Garmin integrations")
        return 1

    # Calculate date range if days specified
    from_date = None
    to_date = None
    if days:
        to_date = datetime.now(UTC)
        from_date = to_date - timedelta(days=days)
        print(f"Backfill window: {from_date.date()} to {to_date.date()} ({days} days)\n")

    # Run backfill for each user
    results = []
    total_imported = 0
    total_skipped = 0
    total_errors = 0
    successful_users = 0
    failed_users = 0

    print("🚀 Starting backfill for all users...\n")

    for idx, user_id in enumerate(user_ids, 1):
        print(f"[{idx}/{len(user_ids)}] Processing user_id: {user_id}")

        try:
            result = backfill_garmin_activities(
                user_id=user_id,
                from_date=from_date,
                to_date=to_date,
                force=force,
            )

            status = result.get("status", "unknown")
            imported = int(result.get("ingested_count", 0))
            skipped = int(result.get("skipped_count", 0))
            errors = int(result.get("error_count", 0))

            total_imported += imported
            total_skipped += skipped
            total_errors += errors

            if errors == 0:
                successful_users += 1
                print(f"  ✅ Success: imported={imported}, skipped={skipped}")
            else:
                failed_users += 1
                print(f"  ⚠️  Completed with errors: imported={imported}, skipped={skipped}, errors={errors}")

            results.append({
                "user_id": user_id,
                "status": status,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "success": errors == 0,
            })

        except Exception as e:
            failed_users += 1
            print(f"  ❌ FAILED: {e}")
            logger.exception(f"Backfill failed for user_id={user_id}")

            results.append({
                "user_id": user_id,
                "status": "error",
                "imported": 0,
                "skipped": 0,
                "errors": 1,
                "success": False,
            })

        print()  # Blank line between users

    # Print summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total users processed: {len(user_ids)}")
    print(f"  ✅ Successful: {successful_users}")
    print(f"  ❌ Failed: {failed_users}")
    print("\nTotal activities:")
    print(f"  Imported: {total_imported}")
    print(f"  Skipped: {total_skipped}")
    print(f"  Errors: {total_errors}")
    print("=" * 70)

    # Show failed users if any
    failed = [r for r in results if not r["success"]]
    if failed:
        print("\n⚠️  Failed users:")
        for r in failed:
            print(f"  - {r['user_id']}: {r['status']} (errors: {r['errors']})")

    # Return error code if any failures
    if failed_users > 0 or total_errors > 0:
        print(f"\n⚠️  WARNING: {failed_users} user(s) failed or had errors")
        return 1

    print("\n✅ SUCCESS: All users processed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
