#!/usr/bin/env python3
"""Trigger Garmin Summary Backfill for all users.

This script:
1. Resets Garmin integration state (garmin_history_requested_at, garmin_history_complete)
2. Triggers Summary Backfill API requests (30-day chunks)
3. Does NOT fetch activities (data arrives via webhooks)

Usage:
    python scripts/reset_and_backfill_garmin.py [--days N] [--reset-state]
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
    """Trigger Summary Backfill for all users."""
    reset_state = "--reset-state" in sys.argv
    days = 90  # Default to 90 days

    if "--days" in sys.argv:
        try:
            days_idx = sys.argv.index("--days")
            days = int(sys.argv[days_idx + 1])
        except (IndexError, ValueError):
            print("Error: --days requires a number")
            return 1

    print("=" * 70)
    print("TRIGGER GARMIN SUMMARY BACKFILL - ALL USERS")
    print("=" * 70)
    print(f"Days to backfill: {days}")
    print(f"Reset state: {reset_state}")
    print("=" * 70)
    print("NOTE: This script triggers backfill requests only.")
    print("      Activities will arrive via webhooks (async).")
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

            # Reset state if requested
            if reset_state:
                print("\n🔄 Resetting Garmin integration state...")
                for integration in integration_objs:
                    old_requested = integration.garmin_history_requested_at
                    old_complete = integration.garmin_history_complete
                    integration.garmin_history_requested_at = None
                    integration.garmin_history_complete = False
                    print(
                        f"  Reset state for {integration.user_id}: "
                        f"requested_at={old_requested}, complete={old_complete} -> None, False"
                    )
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

    # Trigger backfill for each user
    results = []
    total_requests = 0
    total_accepted = 0
    total_duplicates = 0
    total_errors = 0
    successful_users = 0
    failed_users = 0

    print("🚀 Triggering Summary Backfill for all users...\n")

    for idx, user_id in enumerate(user_ids, 1):
        print(f"[{idx}/{len(user_ids)}] Processing user_id: {user_id}")

        try:
            result = backfill_garmin_activities(
                user_id=user_id,
                from_date=from_date,
                to_date=to_date,
                force=True,  # Always force to override any locks
            )

            status = result.get("status", "unknown")
            requests = int(result.get("total_requests", 0))
            accepted = int(result.get("accepted_count", 0))
            duplicates = int(result.get("duplicate_count", 0))
            errors = int(result.get("error_count", 0))

            total_requests += requests
            total_accepted += accepted
            total_duplicates += duplicates
            total_errors += errors

            if errors == 0:
                successful_users += 1
                print(
                    f"  ✅ Success: requests={requests}, accepted={accepted}, "
                    f"duplicates={duplicates}"
                )
            else:
                failed_users += 1
                print(
                    f"  ⚠️  Completed with errors: requests={requests}, "
                    f"accepted={accepted}, duplicates={duplicates}, errors={errors}"
                )

            results.append({
                "user_id": user_id,
                "status": status,
                "requests": requests,
                "accepted": accepted,
                "duplicates": duplicates,
                "errors": errors,
                "success": errors == 0,
            })

        except Exception as e:
            failed_users += 1
            print(f"  ❌ FAILED: {e}")
            logger.exception(f"Backfill trigger failed for user_id={user_id}")

            results.append({
                "user_id": user_id,
                "status": "error",
                "requests": 0,
                "accepted": 0,
                "duplicates": 0,
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
    print("\nBackfill requests:")
    print(f"  Total requests: {total_requests}")
    print(f"  Accepted (202): {total_accepted}")
    print(f"  Duplicates (409): {total_duplicates}")
    print(f"  Errors: {total_errors}")
    print("=" * 70)
    print("\nNOTE: Activities will arrive via webhooks (async).")
    print("      Check webhook logs to see incoming activity data.")
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

    if total_requests == 0:
        print("\n⚠️  WARNING: No backfill requests were triggered. Check:")
        print("  - Garmin API is working")
        print("  - User integrations are active")
        print("  - Date range is valid")
        return 1

    print("\n✅ SUCCESS: Summary Backfill triggered for all users!")
    print("   Activities will arrive via webhooks (check webhook logs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
