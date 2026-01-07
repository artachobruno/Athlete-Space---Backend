#!/usr/bin/env python3
"""Clean corrupted or orphaned user data from the database.

This script identifies and optionally removes:
- Orphaned activities (activities with user_id that doesn't exist in users table)
- Activities with invalid athlete_id mappings
- Orphaned records in related tables (daily_training_load, weekly_training_summary, etc.)
- Legacy StravaAuth records without corresponding StravaAccount
- Invalid or duplicate data

Usage:
    python scripts/clean_user_data.py [--dry-run] [--delete-orphaned] [--fix-invalid]
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import (
    Activity,
    AthleteProfile,
    CoachMessage,
    DailyTrainingLoad,
    PlannedSession,
    StravaAccount,
    StravaAuth,
    User,
    WeeklyTrainingSummary,
)
from app.db.session import engine, get_session


def _analyze_orphaned_activities(session: Session) -> list[dict]:
    """Find activities with user_id that doesn't exist in users table."""
    logger.info("Analyzing orphaned activities...")

    result = session.execute(
        text("""
            SELECT a.id, a.user_id, a.athlete_id, a.strava_activity_id, a.start_time
            FROM activities a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE u.id IS NULL
        """)
    )

    orphaned = [
        {
            "id": row[0],
            "user_id": row[1],
            "athlete_id": row[2],
            "strava_activity_id": row[3],
            "start_time": row[4],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned activities")
    return orphaned


def _analyze_invalid_athlete_mappings(session: Session) -> list[dict]:
    """Find activities with athlete_id that can't be mapped to a valid user_id."""
    logger.info("Analyzing invalid athlete_id mappings...")

    result = session.execute(
        text("""
            SELECT DISTINCT a.athlete_id, COUNT(*) as count
            FROM activities a
            LEFT JOIN strava_accounts sa ON a.athlete_id = sa.athlete_id
            WHERE sa.athlete_id IS NULL
            GROUP BY a.athlete_id
        """)
    )

    invalid = [
        {
            "athlete_id": row[0],
            "count": row[1],
        }
        for row in result
    ]

    logger.info(f"Found {len(invalid)} invalid athlete_id mappings affecting {sum(r['count'] for r in invalid)} activities")
    return invalid


def _analyze_orphaned_training_load(session: Session) -> list[dict]:
    """Find daily_training_load records with invalid user_id."""
    logger.info("Analyzing orphaned daily_training_load records...")

    result = session.execute(
        text("""
            SELECT dtl.id, dtl.user_id, dtl.date
            FROM daily_training_load dtl
            LEFT JOIN users u ON dtl.user_id = u.id
            WHERE u.id IS NULL
        """)
    )

    orphaned = [
        {
            "id": row[0],
            "user_id": row[1],
            "date": row[2],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned daily_training_load records")
    return orphaned


def _analyze_orphaned_weekly_summary(session: Session) -> list[dict]:
    """Find weekly_training_summary records with invalid user_id."""
    logger.info("Analyzing orphaned weekly_training_summary records...")

    result = session.execute(
        text("""
            SELECT wts.id, wts.user_id, wts.week_start
            FROM weekly_training_summary wts
            LEFT JOIN users u ON wts.user_id = u.id
            WHERE u.id IS NULL
        """)
    )

    orphaned = [
        {
            "id": row[0],
            "user_id": row[1],
            "week_start": row[2],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned weekly_training_summary records")
    return orphaned


def _analyze_orphaned_athlete_profiles(session: Session) -> list[dict]:
    """Find athlete_profiles with invalid user_id."""
    logger.info("Analyzing orphaned athlete_profiles...")

    result = session.execute(
        text("""
            SELECT ap.user_id
            FROM athlete_profiles ap
            LEFT JOIN users u ON ap.user_id = u.id
            WHERE u.id IS NULL
        """)
    )

    orphaned = [
        {
            "user_id": row[0],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned athlete_profiles")
    return orphaned


def _analyze_orphaned_planned_sessions(session: Session) -> list[dict]:
    """Find planned_sessions with invalid user_id."""
    logger.info("Analyzing orphaned planned_sessions...")

    result = session.execute(
        text("""
            SELECT ps.id, ps.user_id, ps.date, ps.title
            FROM planned_sessions ps
            LEFT JOIN users u ON ps.user_id = u.id
            WHERE u.id IS NULL
        """)
    )

    orphaned = [
        {
            "id": row[0],
            "user_id": row[1],
            "date": row[2],
            "title": row[3],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned planned_sessions")
    return orphaned


def _analyze_legacy_strava_auth(session: Session) -> list[dict]:
    """Find legacy StravaAuth records without corresponding StravaAccount."""
    logger.info("Analyzing legacy StravaAuth records...")

    result = session.execute(
        text("""
            SELECT sa.athlete_id, sa.last_ingested_at
            FROM strava_auth sa
            LEFT JOIN strava_accounts sc ON CAST(sa.athlete_id AS TEXT) = sc.athlete_id
            WHERE sc.athlete_id IS NULL
        """)
    )

    legacy = [
        {
            "athlete_id": row[0],
            "last_ingested_at": row[1],
        }
        for row in result
    ]

    logger.info(f"Found {len(legacy)} legacy StravaAuth records without StravaAccount")
    return legacy


def _analyze_orphaned_coach_messages(session: Session) -> list[dict]:
    """Find coach_messages with athlete_id that can't be mapped to user_id."""
    logger.info("Analyzing orphaned coach_messages...")

    result = session.execute(
        text("""
            SELECT cm.id, cm.athlete_id, cm.timestamp
            FROM coach_messages cm
            LEFT JOIN strava_accounts sa ON CAST(cm.athlete_id AS TEXT) = sa.athlete_id
            WHERE sa.athlete_id IS NULL
        """)
    )

    orphaned = [
        {
            "id": row[0],
            "athlete_id": row[1],
            "timestamp": row[2],
        }
        for row in result
    ]

    logger.info(f"Found {len(orphaned)} orphaned coach_messages")
    return orphaned


def _delete_orphaned_activities(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned activities."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned activities...")
    deleted = 0

    for item in orphaned:
        try:
            result = session.execute(text("DELETE FROM activities WHERE id = :id"), {"id": item["id"]})
            if result.rowcount > 0:
                deleted += 1
        except Exception as e:
            logger.error(f"Error deleting activity {item['id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned activities")
    return deleted


def _delete_orphaned_training_load(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned daily_training_load records."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned daily_training_load records...")
    deleted = 0

    for item in orphaned:
        try:
            session.execute(text("DELETE FROM daily_training_load WHERE id = :id"), {"id": item["id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting daily_training_load {item['id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned daily_training_load records")
    return deleted


def _delete_orphaned_weekly_summary(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned weekly_training_summary records."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned weekly_training_summary records...")
    deleted = 0

    for item in orphaned:
        try:
            session.execute(text("DELETE FROM weekly_training_summary WHERE id = :id"), {"id": item["id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting weekly_training_summary {item['id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned weekly_training_summary records")
    return deleted


def _delete_orphaned_athlete_profiles(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned athlete_profiles."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned athlete_profiles...")
    deleted = 0

    for item in orphaned:
        try:
            session.execute(text("DELETE FROM athlete_profiles WHERE user_id = :user_id"), {"user_id": item["user_id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting athlete_profile {item['user_id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned athlete_profiles")
    return deleted


def _delete_orphaned_planned_sessions(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned planned_sessions."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned planned_sessions...")
    deleted = 0

    for item in orphaned:
        try:
            session.execute(text("DELETE FROM planned_sessions WHERE id = :id"), {"id": item["id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting planned_session {item['id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned planned_sessions")
    return deleted


def _delete_legacy_strava_auth(session: Session, legacy: list[dict]) -> int:
    """Delete legacy StravaAuth records."""
    if not legacy:
        return 0

    logger.info(f"Deleting {len(legacy)} legacy StravaAuth records...")
    deleted = 0

    for item in legacy:
        try:
            session.execute(text("DELETE FROM strava_auth WHERE athlete_id = :athlete_id"), {"athlete_id": item["athlete_id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting StravaAuth {item['athlete_id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} legacy StravaAuth records")
    return deleted


def _delete_orphaned_coach_messages(session: Session, orphaned: list[dict]) -> int:
    """Delete orphaned coach_messages."""
    if not orphaned:
        return 0

    logger.info(f"Deleting {len(orphaned)} orphaned coach_messages...")
    deleted = 0

    for item in orphaned:
        try:
            session.execute(text("DELETE FROM coach_messages WHERE id = :id"), {"id": item["id"]})
            deleted += 1
        except Exception as e:
            logger.error(f"Error deleting coach_message {item['id']}: {e}")

    session.commit()
    logger.info(f"Deleted {deleted} orphaned coach_messages")
    return deleted


def _print_summary_report(analysis_results: dict[str, list[dict]]) -> None:
    """Print a summary report of all issues found.

    Args:
        analysis_results: Dictionary containing all analysis results with keys:
            - orphaned_activities
            - invalid_athlete_mappings
            - orphaned_training_load
            - orphaned_weekly_summary
            - orphaned_athlete_profiles
            - orphaned_planned_sessions
            - legacy_strava_auth
            - orphaned_coach_messages
    """
    logger.info("=" * 80)
    logger.info("DATA CLEANING SUMMARY REPORT")
    logger.info("=" * 80)

    orphaned_activities = analysis_results.get("orphaned_activities", [])
    invalid_athlete_mappings = analysis_results.get("invalid_athlete_mappings", [])
    orphaned_training_load = analysis_results.get("orphaned_training_load", [])
    orphaned_weekly_summary = analysis_results.get("orphaned_weekly_summary", [])
    orphaned_athlete_profiles = analysis_results.get("orphaned_athlete_profiles", [])
    orphaned_planned_sessions = analysis_results.get("orphaned_planned_sessions", [])
    legacy_strava_auth = analysis_results.get("legacy_strava_auth", [])
    orphaned_coach_messages = analysis_results.get("orphaned_coach_messages", [])

    total_issues = (
        len(orphaned_activities)
        + len(invalid_athlete_mappings)
        + len(orphaned_training_load)
        + len(orphaned_weekly_summary)
        + len(orphaned_athlete_profiles)
        + len(orphaned_planned_sessions)
        + len(legacy_strava_auth)
        + len(orphaned_coach_messages)
    )

    logger.info(f"\nTotal issues found: {total_issues}\n")

    if orphaned_activities:
        logger.warning(f"⚠️  Orphaned activities: {len(orphaned_activities)}")
        logger.info("   Activities with user_id that doesn't exist in users table")
        if len(orphaned_activities) <= 10:
            for item in orphaned_activities[:10]:
                logger.info(f"   - Activity {item['id']}: user_id={item['user_id']}, athlete_id={item['athlete_id']}")

    if invalid_athlete_mappings:
        total_affected = sum(r["count"] for r in invalid_athlete_mappings)
        logger.warning(f"⚠️  Invalid athlete_id mappings: {len(invalid_athlete_mappings)} mappings affecting {total_affected} activities")
        logger.info("   Activities with athlete_id that can't be mapped to StravaAccount")
        for item in invalid_athlete_mappings[:10]:
            logger.info(f"   - athlete_id={item['athlete_id']}: {item['count']} activities")

    if orphaned_training_load:
        logger.warning(f"⚠️  Orphaned daily_training_load: {len(orphaned_training_load)}")

    if orphaned_weekly_summary:
        logger.warning(f"⚠️  Orphaned weekly_training_summary: {len(orphaned_weekly_summary)}")

    if orphaned_athlete_profiles:
        logger.warning(f"⚠️  Orphaned athlete_profiles: {len(orphaned_athlete_profiles)}")

    if orphaned_planned_sessions:
        logger.warning(f"⚠️  Orphaned planned_sessions: {len(orphaned_planned_sessions)}")

    if legacy_strava_auth:
        logger.warning(f"⚠️  Legacy StravaAuth records: {len(legacy_strava_auth)}")
        logger.info("   Old StravaAuth records without corresponding StravaAccount")

    if orphaned_coach_messages:
        logger.warning(f"⚠️  Orphaned coach_messages: {len(orphaned_coach_messages)}")

    if total_issues == 0:
        logger.info("✅ No data integrity issues found!")

    logger.info("=" * 80)


def clean_user_data(dry_run: bool = True, delete_orphaned: bool = False) -> int:
    """Clean corrupted or orphaned user data.

    Args:
        dry_run: If True, only analyze and report issues without making changes
        delete_orphaned: If True, delete orphaned records (only if dry_run=False)

    Returns:
        0 if successful, 1 if errors found
    """
    logger.info("Starting user data cleaning analysis...")

    if dry_run:
        logger.info("🔍 DRY RUN MODE: No changes will be made")
    elif delete_orphaned:
        logger.warning("⚠️  DELETION MODE: Orphaned records will be deleted!")

    with get_session() as session:
        # Analyze all data integrity issues
        orphaned_activities = _analyze_orphaned_activities(session)
        invalid_athlete_mappings = _analyze_invalid_athlete_mappings(session)
        orphaned_training_load = _analyze_orphaned_training_load(session)
        orphaned_weekly_summary = _analyze_orphaned_weekly_summary(session)
        orphaned_athlete_profiles = _analyze_orphaned_athlete_profiles(session)
        orphaned_planned_sessions = _analyze_orphaned_planned_sessions(session)
        legacy_strava_auth = _analyze_legacy_strava_auth(session)
        orphaned_coach_messages = _analyze_orphaned_coach_messages(session)

        # Print summary report
        _print_summary_report({
            "orphaned_activities": orphaned_activities,
            "invalid_athlete_mappings": invalid_athlete_mappings,
            "orphaned_training_load": orphaned_training_load,
            "orphaned_weekly_summary": orphaned_weekly_summary,
            "orphaned_athlete_profiles": orphaned_athlete_profiles,
            "orphaned_planned_sessions": orphaned_planned_sessions,
            "legacy_strava_auth": legacy_strava_auth,
            "orphaned_coach_messages": orphaned_coach_messages,
        })

        # Delete orphaned records if requested
        if not dry_run and delete_orphaned:
            logger.info("\n" + "=" * 80)
            logger.info("DELETING ORPHANED RECORDS")
            logger.info("=" * 80)

            deleted_count = 0
            deleted_count += _delete_orphaned_activities(session, orphaned_activities)
            deleted_count += _delete_orphaned_training_load(session, orphaned_training_load)
            deleted_count += _delete_orphaned_weekly_summary(session, orphaned_weekly_summary)
            deleted_count += _delete_orphaned_athlete_profiles(session, orphaned_athlete_profiles)
            deleted_count += _delete_orphaned_planned_sessions(session, orphaned_planned_sessions)
            deleted_count += _delete_legacy_strava_auth(session, legacy_strava_auth)
            deleted_count += _delete_orphaned_coach_messages(session, orphaned_coach_messages)

            logger.info(f"\n✅ Deleted {deleted_count} orphaned records")
        elif delete_orphaned:
            logger.warning("⚠️  Cannot delete in dry-run mode. Run without --dry-run to delete.")

    logger.info("Data cleaning analysis complete")
    return 0


def main() -> int:
    """Main entry point."""
    parser = ArgumentParser(description="Clean corrupted or orphaned user data")
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually make changes (default is dry-run mode)",
    )
    parser.add_argument(
        "--delete-orphaned",
        action="store_true",
        help="Delete orphaned records (requires --no-dry-run)",
    )

    args = parser.parse_args()

    dry_run = not args.no_dry_run

    # Safety check: require explicit --no-dry-run to allow deletions
    if args.delete_orphaned and dry_run:
        logger.error("❌ Cannot use --delete-orphaned in dry-run mode")
        logger.error("   Use --no-dry-run --delete-orphaned to delete records")
        return 1

    return clean_user_data(dry_run=dry_run, delete_orphaned=args.delete_orphaned)


if __name__ == "__main__":
    sys.exit(main())
