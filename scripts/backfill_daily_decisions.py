"""Backfill daily decisions for users.

This script generates daily decisions for missing dates, typically used after
debugging or fixing issues that prevented decisions from being generated.

Supports:
- All users or specific user_id
- Date range (defaults to last 30 days)
- Regenerating existing decisions (optional)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import StravaAccount
from app.db.session import get_session
from app.services.intelligence.scheduler import trigger_daily_decision_for_user
from app.services.intelligence.store import IntentStore


def get_all_users_with_strava() -> list[tuple[str, int]]:
    """Get all users with connected Strava accounts.

    Returns:
        List of (user_id, athlete_id) tuples
    """
    with get_session() as session:
        accounts = session.execute(select(StravaAccount)).scalars().all()
        # Ensure user_id is always a string (not UUID) to match database column type
        user_accounts = [(str(acc.user_id), int(acc.athlete_id)) for acc in accounts]
        logger.info(f"Found {len(user_accounts)} users with connected Strava accounts")
        return user_accounts


async def backfill_daily_decisions_for_user(
    user_id: str,
    athlete_id: int,
    start_date: date,
    end_date: date,
    regenerate_existing: bool = False,
) -> dict[str, int]:
    """Backfill daily decisions for a specific user over a date range.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        regenerate_existing: If True, regenerate even if decision exists

    Returns:
        Dictionary with counts of decisions processed
    """
    # Ensure user_id is always a string (not UUID) to match database column type
    user_id = str(user_id)

    store = IntentStore()
    current_date = start_date
    success_count = 0
    skipped_count = 0
    error_count = 0

    logger.info(
        f"Backfilling daily decisions for user_id={user_id}, "
        f"athlete_id={athlete_id}, date_range=[{start_date.isoformat()}, {end_date.isoformat()}]"
    )

    while current_date <= end_date:
        decision_date_dt = datetime.combine(current_date, datetime.min.time()).replace(tzinfo=UTC)
        existing = store.get_latest_daily_decision(user_id, decision_date_dt, active_only=True)

        if existing and not regenerate_existing:
            logger.debug(
                f"  Decision already exists for {current_date.isoformat()}, skipping "
                f"(use --regenerate to force regeneration)"
            )
            skipped_count += 1
            current_date += timedelta(days=1)
            continue

        try:
            await trigger_daily_decision_for_user(user_id, athlete_id, current_date)
            success_count += 1
            logger.debug(f"  ✅ Generated decision for {current_date.isoformat()}")
        except Exception:
            logger.exception(
                f"  ❌ Failed to generate decision for {current_date.isoformat()}, user_id={user_id}"
            )
            error_count += 1

        current_date += timedelta(days=1)

    logger.info(
        f"Completed backfill for user_id={user_id}: "
        f"success={success_count}, skipped={skipped_count}, errors={error_count}"
    )

    return {
        "success": success_count,
        "skipped": skipped_count,
        "errors": error_count,
    }


async def backfill_daily_decisions_for_all_users(
    start_date: date,
    end_date: date,
    regenerate_existing: bool = False,
) -> dict[str, int]:
    """Backfill daily decisions for all users over a date range.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        regenerate_existing: If True, regenerate even if decision exists

    Returns:
        Dictionary with aggregate counts across all users
    """
    user_accounts = get_all_users_with_strava()
    total_users = len(user_accounts)

    logger.info(
        f"Starting daily decisions backfill for {total_users} users, "
        f"date_range=[{start_date.isoformat()}, {end_date.isoformat()}]"
    )

    total_success = 0
    total_skipped = 0
    total_errors = 0

    for idx, (user_id, athlete_id) in enumerate(user_accounts, 1):
        logger.info(f"Processing user {idx}/{total_users}: user_id={user_id}")
        try:
            results = await backfill_daily_decisions_for_user(
                user_id, athlete_id, start_date, end_date, regenerate_existing
            )
            total_success += results["success"]
            total_skipped += results["skipped"]
            total_errors += results["errors"]
        except Exception:
            logger.exception(f"Fatal error processing user_id={user_id}")
            total_errors += (end_date - start_date).days + 1

    logger.info(
        f"Completed backfill for all users: "
        f"total_success={total_success}, total_skipped={total_skipped}, total_errors={total_errors}"
    )

    return {
        "success": total_success,
        "skipped": total_skipped,
        "errors": total_errors,
    }


def main() -> None:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(description="Backfill daily decisions for users")
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Specific user ID to backfill (default: all users)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD, default: 30 days ago)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate decisions even if they already exist",
    )

    args = parser.parse_args()

    # Parse dates (use date.fromisoformat to avoid timezone-aware datetime warnings)
    end_date = datetime.now(UTC).date()
    if args.end_date:
        end_date = date.fromisoformat(args.end_date)

    start_date = end_date - timedelta(days=30)
    if args.start_date:
        start_date = date.fromisoformat(args.start_date)

    if start_date > end_date:
        logger.error(f"Start date ({start_date}) must be <= end date ({end_date})")
        sys.exit(1)

    logger.info(
        f"Starting daily decisions backfill: "
        f"user_id={args.user_id or 'ALL'}, "
        f"date_range=[{start_date.isoformat()}, {end_date.isoformat()}], "
        f"regenerate={args.regenerate}"
    )

    if args.user_id:
        # Backfill for specific user - need to get athlete_id
        # Ensure user_id is always a string (not UUID) to match database column type
        user_id_str = str(args.user_id)
        with get_session() as session:
            account = session.execute(
                select(StravaAccount).where(StravaAccount.user_id == user_id_str)
            ).scalar_one_or_none()

            if account is None:
                logger.error(f"No Strava account found for user_id={user_id_str}")
                sys.exit(1)

            athlete_id = int(account.athlete_id)
            results = asyncio.run(
                backfill_daily_decisions_for_user(
                    user_id_str, athlete_id, start_date, end_date, args.regenerate
                )
            )
    else:
        # Backfill for all users
        results = asyncio.run(
            backfill_daily_decisions_for_all_users(start_date, end_date, args.regenerate)
        )

    logger.info("✅ Backfill completed!")
    logger.info(f"   Success: {results['success']}")
    logger.info(f"   Skipped: {results['skipped']}")
    logger.info(f"   Errors: {results['errors']}")


if __name__ == "__main__":
    main()
