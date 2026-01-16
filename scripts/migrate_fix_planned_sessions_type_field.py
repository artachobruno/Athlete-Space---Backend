"""Migration script to fix planned_sessions.type field.

This script fixes planned sessions that have workout types (easy, long, threshold, etc.)
in the `type` field instead of sport types (Run, Bike, Swim, etc.).

It:
1. Finds planned sessions with incorrect type values (workout types instead of sport types)
2. Updates them to have proper sport types based on title or defaults to "Run"
3. Preserves the workout type in the `session_type` or `intent` field if available

Usage:
    From project root:
    python scripts/migrate_fix_planned_sessions_type_field.py [--no-dry-run] [--user-id USER_ID]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.db.session import SessionLocal

# Workout types that should NOT be in the type field
WORKOUT_TYPES = {
    "easy", "long", "threshold", "tempo", "interval", "vo2", "fartlek",
    "recovery", "rest", "race", "moderate", "hard", "quality", "hills",
    "strides", "aerobic", "steady", "marathon", "economy", "speed",
    "workout",
}


def _normalize_sport_type(type_raw: str | None) -> str:
    """Normalize type field to be a sport type (Run, Bike, Swim, etc.).

    Args:
        type_raw: Raw type value (may be sport type or workout type)

    Returns:
        Normalized sport type (defaults to "Run" if None or invalid)
    """
    if not type_raw:
        return "Run"

    type_lower = type_raw.lower().strip()

    # Sport types - return normalized (capitalized)
    sport_types: dict[str, str] = {
        "run": "Run",
        "running": "Run",
        "ride": "Bike",
        "bike": "Bike",
        "biking": "Bike",
        "cycling": "Bike",
        "cycle": "Bike",
        "swim": "Swim",
        "swimming": "Swim",
        "tri": "Triathlon",
        "triathlon": "Triathlon",
        "crossfit": "Crossfit",
        "strength": "Strength",
        "walk": "Walk",
        "walking": "Walk",
    }

    if type_lower in sport_types:
        return sport_types[type_lower]

    # Unknown type - capitalize first letter as fallback
    return type_raw.capitalize()


def fix_planned_sessions_type(
    db: Session,
    user_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Fix planned sessions with incorrect type values.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        dry_run: If True, only log what would be done without making changes

    Returns:
        Dictionary with statistics about the fixes
    """
    stats: dict[str, int] = {
        "sessions_found": 0,
        "sessions_fixed": 0,
        "sessions_skipped": 0,
        "errors": 0,
    }

    # Build query for planned sessions with incorrect type values
    query = select(PlannedSession)

    if user_id:
        query = query.where(PlannedSession.user_id == user_id)

    all_sessions = list(db.scalars(query).all())
    stats["sessions_found"] = len(all_sessions)

    # Filter sessions with workout types in the type field
    sessions_to_fix = []
    for session in all_sessions:
        if not session.type:
            continue
        type_lower = session.type.lower().strip()
        if type_lower in WORKOUT_TYPES:
            sessions_to_fix.append(session)

    logger.info(
        f"Found {len(sessions_to_fix)} planned sessions with incorrect type values"
        f"{f' for user {user_id}' if user_id else ''}"
        f" (out of {len(all_sessions)} total sessions)"
    )

    for session in sessions_to_fix:
        try:
            old_type = session.type
            title = session.title or ""

            # Normalize to sport type
            new_type = _normalize_sport_type(old_type)

            # If still a workout type after normalization, try to infer from title
            if new_type.lower() in WORKOUT_TYPES:
                # Try to infer from title
                title_lower = title.lower()
                if any(word in title_lower for word in ["bike", "ride", "cycling", "cycle"]):
                    new_type = "Bike"
                elif any(word in title_lower for word in ["swim", "swimming"]):
                    new_type = "Swim"
                elif any(word in title_lower for word in ["tri", "triathlon"]):
                    new_type = "Triathlon"
                else:
                    new_type = "Run"  # Default

            if new_type.lower() == old_type.lower():
                stats["sessions_skipped"] += 1
                logger.debug(
                    f"Skipping session {session.id} - type '{old_type}' normalized to same value"
                )
                continue

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would fix session {session.id}: "
                    f"type '{old_type}' -> '{new_type}' "
                    f"(title: '{title}')"
                )
                stats["sessions_fixed"] += 1
            else:
                # Update the type field
                session.type = new_type

                # If session_type is not set, preserve the old type value there
                if not session.session_type and old_type not in WORKOUT_TYPES:
                    # Old type wasn't a workout type, so it might have been a sport type
                    # Keep it in session_type as backup
                    pass
                elif not session.session_type:
                    # Old type was a workout type - preserve it in session_type
                    session.session_type = old_type

                logger.info(
                    f"✅ Fixed session {session.id}: "
                    f"type '{old_type}' -> '{new_type}' "
                    f"(title: '{title}')"
                )
                stats["sessions_fixed"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error fixing session {session.id}: {e}",
                exc_info=True,
            )

    if not dry_run:
        db.commit()
        logger.info("Changes committed to database")

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Fix planned_sessions.type field (workout types -> sport types)"
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
        help="Filter by specific user ID (optional)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Fix Planned Sessions Type Field Migration")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        stats = fix_planned_sessions_type(
            db=db,
            user_id=args.user_id,
            dry_run=dry_run,
        )

        logger.info("=" * 80)
        logger.info("Summary:")
        logger.info(f"  Total sessions checked: {stats['sessions_found']}")
        logger.info(f"  Sessions with incorrect types: {stats['sessions_fixed'] + stats['sessions_skipped']}")
        if dry_run:
            logger.info(f"  Would fix: {stats['sessions_fixed']}")
            logger.info("  (Run with --no-dry-run to actually fix)")
        else:
            logger.info(f"  Fixed: {stats['sessions_fixed']}")
            logger.info(f"  Skipped: {stats['sessions_skipped']}")
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
