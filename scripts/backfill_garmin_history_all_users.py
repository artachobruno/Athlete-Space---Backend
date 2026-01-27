#!/usr/bin/env python3
"""Run historical backfill for ALL users with Garmin integrations.

This uses the history backfill endpoint which processes 90-day chunks
going backwards in time. It's safer for large date ranges.

Usage on Render:
    python scripts/backfill_garmin_history_all_users.py [--chunks N]

Examples:
    python scripts/backfill_garmin_history_all_users.py
    python scripts/backfill_garmin_history_all_users.py --chunks 5
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import UserIntegration
from app.db.session import get_session
from app.integrations.garmin.history_backfill import backfill_garmin_history_chunk


def main() -> int:
    """Run history backfill for all users."""
    chunks_per_user = 1
    if "--chunks" in sys.argv:
        try:
            chunks_idx = sys.argv.index("--chunks")
            chunks_per_user = int(sys.argv[chunks_idx + 1])
        except (IndexError, ValueError):
            print("Error: --chunks requires a number")
            return 1

    print("=" * 70)
    print("GARMIN HISTORY BACKFILL - ALL USERS")
    print("=" * 70)
    print(f"Chunks per user: {chunks_per_user} (90 days each)")
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

    # Run history backfill for each user
    results = []
    total_imported = 0
    total_skipped = 0
    total_errors = 0
    successful_users = 0
    failed_users = 0
    complete_users = 0

    print("🚀 Starting history backfill for all users...\n")

    for idx, user_id in enumerate(user_ids, 1):
        print(f"[{idx}/{len(user_ids)}] Processing user_id: {user_id}")

        user_imported = 0
        user_skipped = 0
        user_errors = 0
        user_complete = False

        try:
            for chunk_num in range(1, chunks_per_user + 1):
                print(f"  Chunk {chunk_num}/{chunks_per_user}...", end=" ", flush=True)

                result = backfill_garmin_history_chunk(user_id)

                imported = result.get("ingested_count", 0)
                skipped = result.get("skipped_count", 0)
                errors = result.get("error_count", 0)
                complete = result.get("complete", False)

                user_imported += imported
                user_skipped += skipped
                user_errors += errors
                user_complete = complete

                if complete:
                    print(f"✅ Complete (imported={imported}, skipped={skipped})")
                    break
                if errors > 0:
                    print(f"⚠️  Errors: {errors} (imported={imported}, skipped={skipped})")
                else:
                    print(f"✓ (imported={imported}, skipped={skipped})")

                # Stop if complete
                if complete:
                    break

            total_imported += user_imported
            total_skipped += user_skipped
            total_errors += user_errors

            if user_errors == 0:
                successful_users += 1
                if user_complete:
                    complete_users += 1
                    print(f"  ✅ Complete: imported={user_imported}, skipped={user_skipped}")
                else:
                    print(f"  ✅ Progress: imported={user_imported}, skipped={user_skipped} (not complete)")
            else:
                failed_users += 1
                print(f"  ⚠️  Completed with errors: imported={user_imported}, skipped={user_skipped}, errors={user_errors}")

            results.append({
                "user_id": user_id,
                "status": "complete" if user_complete else "partial",
                "imported": user_imported,
                "skipped": user_skipped,
                "errors": user_errors,
                "complete": user_complete,
                "success": user_errors == 0,
            })

        except Exception as e:
            failed_users += 1
            print(f"  ❌ FAILED: {e}")
            logger.exception(f"History backfill failed for user_id={user_id}")

            results.append({
                "user_id": user_id,
                "status": "error",
                "imported": 0,
                "skipped": 0,
                "errors": 1,
                "complete": False,
                "success": False,
            })

        print()  # Blank line between users

    # Print summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total users processed: {len(user_ids)}")
    print(f"  ✅ Successful: {successful_users}")
    print(f"  ✅ Complete: {complete_users}")
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
