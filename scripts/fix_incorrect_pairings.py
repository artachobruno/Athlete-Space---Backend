"""Script to fix incorrect pairings after user_id mismatches are resolved.

This script finds planned sessions that are paired with activities that have
mismatched user_ids (after user_id fixes), unpairs them, and re-pairs them
with the correct activities.

Usage:
    From project root:
    python scripts/fix_incorrect_pairings.py [--no-dry-run] [--user-id USER_ID]

    Or as a module:
    python -m scripts.fix_incorrect_pairings [--no-dry-run] [--user-id USER_ID]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
    - Can filter by specific user_id for testing
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

from datetime import UTC, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PairingDecision, PlannedSession
from app.db.session import SessionLocal
from app.pairing.auto_pairing_service import try_auto_pair

# Import Workout to ensure metadata is loaded for foreign key resolution
from app.workouts.models import Workout

_ = Workout.__table__  # Force metadata loading


def find_and_fix_incorrect_pairings(
    db: Session,
    user_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Find and fix incorrect pairings after user_id fixes.

    Args:
        db: Database session
        user_id: Optional user_id to filter by (for testing)
        dry_run: If True, only log what would be done without making changes

    Returns:
        Dictionary with statistics about the processing
    """
    stats: dict[str, int] = {
        "pairings_checked": 0,
        "mismatched_pairings": 0,
        "unpaired": 0,
        "re_paired": 0,
        "failed": 0,
        "errors": 0,
    }

    # Find all planned sessions that are paired
    query = select(PlannedSession).where(
        PlannedSession.completed_activity_id.isnot(None)
    )

    if user_id:
        query = query.where(PlannedSession.user_id == user_id)

    paired_sessions = list(db.scalars(query).all())
    stats["pairings_checked"] = len(paired_sessions)

    logger.info(
        f"Found {len(paired_sessions)} paired planned sessions"
        f"{f' for user {user_id}' if user_id else ''}"
    )

    mismatched = []

    for planned in paired_sessions:
        try:
            # Get the paired activity
            activity = db.get(Activity, planned.completed_activity_id)
            if not activity:
                logger.warning(
                    f"Planned session {planned.id} references activity {planned.completed_activity_id} "
                    f"that doesn't exist"
                )
                stats["errors"] += 1
                continue

            # Check if user_ids match
            if planned.user_id != activity.user_id:
                mismatched.append((planned, activity))
                stats["mismatched_pairings"] += 1

                logger.info(
                    f"Found mismatched pairing: "
                    f"planned_session_id={planned.id}, "
                    f"planned_user_id={planned.user_id}, "
                    f"activity_id={activity.id}, "
                    f"activity_user_id={activity.user_id}, "
                    f"athlete_id={planned.athlete_id}, "
                    f"date={planned.date.date()}"
                )

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error checking planned session {planned.id}: {e}",
                exc_info=True,
            )

    logger.info(f"Found {len(mismatched)} mismatched pairings")

    # Fix mismatched pairings
    for planned, old_activity in mismatched:
        try:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would unpair planned session {planned.id} from activity {old_activity.id} "
                    f"and attempt to re-pair with correct activity"
                )
                stats["unpaired"] += 1
                stats["re_paired"] += 1
            else:
                # Unpair the incorrect pairing directly
                try:
                    # Clear bidirectional links
                    old_activity.planned_session_id = None
                    planned.completed_activity_id = None

                    # Log the unpairing decision
                    pairing_decision = PairingDecision(
                        user_id=planned.user_id,
                        planned_session_id=planned.id,
                        activity_id=old_activity.id,
                        decision="manual_unpair",
                        duration_diff_pct=None,
                        reason="fix_user_id_mismatch",
                        created_at=datetime.now(UTC),
                    )
                    db.add(pairing_decision)

                    db.commit()
                    stats["unpaired"] += 1
                    logger.info(
                        f"✅ Unpaired planned session {planned.id} from incorrect activity {old_activity.id}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to unpair {planned.id} from {old_activity.id}: {e}",
                        exc_info=True,
                    )
                    db.rollback()
                    stats["failed"] += 1
                    continue

                # Refresh to get updated state
                db.refresh(planned)

                # Attempt to re-pair with correct activity
                try:
                    try_auto_pair(planned=planned, session=db)
                    db.commit()

                    # Check if pairing succeeded
                    db.refresh(planned)
                    if planned.completed_activity_id:
                        stats["re_paired"] += 1
                        logger.info(
                            f"✅ Re-paired planned session {planned.id} with activity {planned.completed_activity_id}"
                        )
                    else:
                        logger.warning(
                            f"⚠️  Planned session {planned.id} could not be re-paired automatically"
                        )
                        stats["failed"] += 1

                except Exception as e:
                    logger.error(
                        f"Failed to re-pair planned session {planned.id}: {e}",
                        exc_info=True,
                    )
                    db.rollback()
                    stats["failed"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error fixing pairing for planned session {planned.id}: {e}",
                exc_info=True,
            )
            db.rollback()

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Fix incorrect pairings after user_id mismatches are resolved"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the fixes (default: dry-run mode)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user_id (for testing)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Fix Incorrect Pairings Script")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        stats = find_and_fix_incorrect_pairings(
            db=db,
            user_id=args.user_id,
            dry_run=dry_run,
        )

        logger.info("=" * 80)
        logger.info("Summary:")
        logger.info(f"  Pairings checked: {stats['pairings_checked']}")
        logger.info(f"  Mismatched pairings found: {stats['mismatched_pairings']}")
        if dry_run:
            logger.info("  (Run with --no-dry-run to actually fix)")
        else:
            logger.info(f"  Unpaired: {stats['unpaired']}")
            logger.info(f"  Re-paired: {stats['re_paired']}")
            logger.info(f"  Failed to re-pair: {stats['failed']}")
            logger.info(f"  Errors: {stats['errors']}")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
