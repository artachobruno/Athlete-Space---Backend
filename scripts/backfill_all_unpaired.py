"""Comprehensive backfill script to pair all unpaired activities and planned sessions.

This script:
1. Processes all unpaired activities and attempts to pair them (standard mode)
2. Falls back to relaxed mode (1:1 date+sport) for remaining unpaired items
3. Handles cases with multiple planned sessions on the same day

Usage:
    python scripts/backfill_all_unpaired.py [--no-dry-run] [--user-id USER_ID] [--days DAYS]

Safety:
    - DRY_RUN = True by default
    - Use --no-dry-run to actually execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger
from app.db.session import SessionLocal
from scripts.backfill_unpaired_activities import (
    process_unpaired_activities,
    process_relaxed_date_sport_1to1,
)


def main() -> None:
    """Main entry point for comprehensive backfill."""
    parser = argparse.ArgumentParser(
        description="Comprehensive backfill to pair all unpaired activities and planned sessions"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the pairing (default: dry-run mode)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user ID (optional)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only process activities from the last N days (optional)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Comprehensive Backfill: Pair All Unpaired Activities")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    if args.days:
        logger.info(f"Filter: last {args.days} days")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        # Step 1: Standard backfill (duration-based matching)
        logger.info("Step 1: Standard backfill (duration-based matching)")
        stats1 = process_unpaired_activities(
            db=db,
            user_id=args.user_id,
            days=args.days,
            dry_run=dry_run,
        )
        logger.info("=" * 80)
        logger.info("Step 1 Summary:")
        logger.info(f"  Activities found: {stats1['activities_found']}")
        if dry_run:
            logger.info(f"  Would attempt to pair: {stats1['activities_found']}")
        else:
            logger.info(f"  Successfully paired: {stats1['paired']}")
            logger.info(f"  Failed to pair: {stats1['failed']}")
        logger.info("=" * 80)

        # Step 2: Relaxed backfill (1:1 date+sport, no duration check)
        # This handles cases where duration doesn't match but there's exactly one unpaired plan and one unpaired activity
        logger.info("Step 2: Relaxed backfill (1:1 date+sport matching)")
        stats2 = process_relaxed_date_sport_1to1(
            db=db,
            user_id=args.user_id,
            days=args.days,
            dry_run=dry_run,
        )
        logger.info("=" * 80)
        logger.info("Step 2 Summary (relaxed):")
        logger.info(f"  1:1 pairs found: {stats2['pairs_found']}")
        if dry_run:
            logger.info(f"  Would pair: {stats2['pairs_found']}")
        else:
            logger.info(f"  Successfully paired: {stats2['paired']}")
            logger.info(f"  Failed: {stats2['failed']}")
        logger.info("=" * 80)

        # Final summary
        logger.info("=" * 80)
        logger.info("FINAL SUMMARY")
        logger.info("=" * 80)
        if dry_run:
            total_would_pair = stats1['activities_found'] + stats2['pairs_found']
            logger.info(f"  Total would pair: {total_would_pair}")
            logger.info("  (Run with --no-dry-run to actually pair)")
        else:
            total_paired = stats1['paired'] + stats2['paired']
            total_failed = stats1['failed'] + stats2['failed']
            logger.info(f"  Total successfully paired: {total_paired}")
            logger.info(f"  Total failed: {total_failed}")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
