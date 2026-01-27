#!/usr/bin/env python3
"""Reset Garmin backfill cursor and run fresh backfill for all users.

This script:
1. Resets the historical_backfill_cursor_date to None (or a recent date)
2. Runs fresh backfill for recent activities (last 90 days)
3. Optionally resets last_sync_at to force re-sync

Usage on Render:
    python scripts/reset_and_backfill_garmin.py [--days N] [--reset-cursor] [--reset-sync]
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
    """Reset cursor and run backfill."""
    reset_cursor = "--reset-cursor" in sys.argv
    reset_sync = "--reset-sync" in sys.argv
    days = 90  # Default to 90 days

    if "--days" in sys.argv:
        try:
            days_idx = sys.argv.index("--days")
            days = int(sys.argv[days_idx + 1])
        except (IndexError, ValueError):
            print("Error: --days requires a number")
            return 1

    print("=" * 70)
    print("RESET AND BACKFILL GARMIN - ALL USERS")
    print("=" * 70)
    print(f"Days to backfill: {days}")
    print(f"Reset cursor: {reset_cursor}")
    print(f"Reset last_sync_at: {reset_sync}")
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
            integration_objs = [integration[0] for integration in integrations]
            print(f"\n✓ Found {len(user_ids)} user(s) with Garmin integrations")

            # Reset cursors if requested
            if reset_cursor or reset_sync:
                print("\n🔄 Resetting integration state...")
                for integration in integration_objs:
                    if reset_cursor:
                        old_cursor = integration.historical_backfill_cursor_date
                        integration.historical_backfill_cursor_date = None
                        integration.historical_backfill_complete = False
                        print(f"  Reset cursor for {integration.user_id}: {old_cursor} -> None")
                    if reset_sync:
                        old_sync = integration.last_sync_at
                        integration.last_sync_at = None
                        print(f"  Reset last_sync_at for {integration.user_id}: {old_sync} -> None")
                session.commit()
                print("✓ Reset complete\n")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Failed to reset integrations")
        return 1

    # Calculate date range
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
                force=True,  # Always force to override any sync locks
            )

            status = result.get("status", "unknown")
            imported = result.get("ingested_count", 0)
            skipped = result.get("skipped_count", 0)
            errors = result.get("error_count", 0)

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

    # Show users with no imports
    no_imports = [r for r in results if r["imported"] == 0 and r["success"]]
    if no_imports:
        print("\n⚠️  Users with no imports (might be normal if no activities in date range):")
        for r in no_imports:
            print(f"  - {r['user_id']}: {r['status']}")

    # Return error code if any failures
    if failed_users > 0 or total_errors > 0:
        print(f"\n⚠️  WARNING: {failed_users} user(s) failed or had errors")
        return 1

    if total_imported == 0:
        print("\n⚠️  WARNING: No activities were imported. Check:")
        print("  - Garmin API is working")
        print("  - User has activities in the date range")
        print("  - Date format is correct")
        return 1

    print("\n✅ SUCCESS: Backfill completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
