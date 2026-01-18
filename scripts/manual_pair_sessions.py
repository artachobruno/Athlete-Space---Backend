"""Script to manually trigger pairing for planned sessions and activities.

This script can be used to:
1. Pair specific planned sessions with activities
2. Re-run auto-pairing for sessions that should be paired
3. Backfill workouts, executions, and compliance for already-paired sessions
4. Debug pairing issues

Usage:
    python scripts/manual_pair_sessions.py --user-id <user_id> --planned-session-id <id>
    python scripts/manual_pair_sessions.py --user-id <user_id> --date-range 2026-01-01 2026-01-31
    python scripts/manual_pair_sessions.py --user-id <user_id> --all-unpaired
    python scripts/manual_pair_sessions.py --user-id <user_id> --backfill-generation
"""

import argparse
import sys
from datetime import UTC, date, datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.db.models import Activity, PlannedSession
from app.db.session import get_session
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_planned
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout
from app.workouts.workout_factory import WorkoutFactory


def pair_specific_session(planned_session_id: str, user_id: str) -> None:
    """Pair a specific planned session with matching activities."""
    with get_session() as session:
        planned = session.scalar(
            select(PlannedSession)
            .where(PlannedSession.id == planned_session_id)
            .where(PlannedSession.user_id == user_id)
        )

        if not planned:
            print(f"❌ Planned session {planned_session_id} not found")
            return

        print(f"📅 Found planned session: {planned.title} on {planned.starts_at}")
        print(f"   Duration: {planned.duration_seconds}s ({planned.duration_minutes} min)")
        print(f"   Distance: {planned.distance_meters}m ({planned.distance_km} km)")
        print(f"   Sport: {planned.sport}")
        print(f"   Status: {planned.status}")

        try:
            try_auto_pair(planned=planned, session=session)
            session.commit()
            print(f"✅ Auto-pairing completed for {planned_session_id}")
        except Exception as e:
            session.rollback()
            print(f"❌ Auto-pairing failed: {e}")
            raise


def _ensure_generation_for_paired_session(
    db_session, planned: PlannedSession, activity: Activity
) -> dict[str, bool]:
    """Ensure workout, execution, and compliance exist for a paired session.

    Returns:
        Dict with 'workout_created', 'execution_created', 'compliance_created' flags
    """
    results = {
        "workout_created": False,
        "execution_created": False,
        "compliance_created": False,
    }

    # Ensure workout exists
    if not planned.workout_id:
        try:
            workout = WorkoutFactory.get_or_create_for_planned_session(db_session, planned)
            results["workout_created"] = True
            print(f"      ✅ Created workout: {workout.id}")
        except Exception as e:
            print(f"      ⚠️  Failed to create workout: {e}")
            return results
    else:
        workout = db_session.execute(
            select(Workout).where(Workout.id == planned.workout_id)
        ).scalar_one_or_none()
        if not workout:
            print(f"      ⚠️  Workout {planned.workout_id} not found, creating new one")
            workout = WorkoutFactory.get_or_create_for_planned_session(db_session, planned)
            results["workout_created"] = True

    if not workout:
        return results

    # Ensure execution exists
    existing_execution = db_session.execute(
        select(WorkoutExecution).where(
            WorkoutExecution.workout_id == workout.id,
            WorkoutExecution.activity_id == activity.id,
        )
    ).scalar_one_or_none()

    if not existing_execution:
        try:
            execution = WorkoutFactory.attach_activity(
                db_session, workout, activity, planned_session_id=planned.id
            )
            results["execution_created"] = True
            print(f"      ✅ Created execution: {execution.id}")
        except Exception as e:
            print(f"      ⚠️  Failed to create execution: {e}")
            return results

    # Ensure compliance exists
    existing_compliance = db_session.execute(
        select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == workout.id)
    ).scalar_one_or_none()

    if not existing_compliance:
        try:
            ComplianceService.compute_and_persist(db_session, workout.id)
            results["compliance_created"] = True
            print("      ✅ Created compliance summary")
        except Exception as e:
            print(f"      ⚠️  Failed to create compliance: {e}")

    return results


def _handle_backfill_for_paired_session(db_session, planned: PlannedSession, link) -> bool:
    """Handle backfilling generation for an already-paired session.

    Returns:
        True if backfill was successful, False otherwise
    """
    activity = db_session.execute(
        select(Activity).where(Activity.id == link.activity_id)
    ).scalar_one_or_none()

    if not activity:
        print(f"   ⚠️  Activity {link.activity_id} not found")
        return False

    print(f"      Already paired with activity {activity.id}, backfilling generation...")
    results = _ensure_generation_for_paired_session(db_session, planned, activity)
    db_session.commit()
    if any(results.values()):
        print("   ✅ Backfill completed")
        return True
    print("   i  All generation data already exists")
    return False


def _process_session_pairing(db_session, planned: PlannedSession, backfill: bool) -> tuple[bool, bool]:
    """Process a single session for pairing or backfilling.

    Returns:
        Tuple of (was_paired, was_backfilled)
    """
    link = get_link_for_planned(db_session, planned.id)
    print(f"\n🔍 Processing: {planned.title} on {planned.starts_at.date()}")

    if link and backfill:
        was_backfilled = _handle_backfill_for_paired_session(db_session, planned, link)
        return False, was_backfilled

    try_auto_pair(planned=planned, session=db_session)
    db_session.commit()
    print("   ✅ Pairing attempted")
    return True, False


def pair_sessions_in_date_range(user_id: str, start_date: date, end_date: date, backfill: bool = False) -> None:
    """Pair all sessions in a date range, optionally backfilling generation for already-paired sessions."""
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC)
    end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC)

    with get_session() as db_session:
        sessions = db_session.scalars(
            select(PlannedSession)
            .where(PlannedSession.user_id == user_id)
            .where(PlannedSession.starts_at >= start_datetime)
            .where(PlannedSession.starts_at <= end_datetime)
            .order_by(PlannedSession.starts_at)
        ).all()

        print(f"📅 Found {len(sessions)} planned sessions in range {start_date} to {end_date}")

        paired_count = 0
        backfilled_count = 0
        error_count = 0

        for planned in sessions:
            try:
                was_paired, was_backfilled = _process_session_pairing(db_session, planned, backfill)
                if was_paired:
                    paired_count += 1
                if was_backfilled:
                    backfilled_count += 1
            except Exception as e:
                db_session.rollback()
                error_count += 1
                print(f"   ❌ Error: {e}")

        print(f"\n📊 Summary: {paired_count} paired, {backfilled_count} backfilled, {error_count} errors")


def pair_all_unpaired(user_id: str) -> None:
    """Pair all unpaired sessions for a user."""
    with get_session() as session:
        all_sessions = session.scalars(
            select(PlannedSession)
            .where(PlannedSession.user_id == user_id)
            .order_by(PlannedSession.starts_at)
        ).all()

        # Filter to unpaired sessions
        unpaired = []
        for planned in all_sessions:
            link = get_link_for_planned(session, planned.id)
            if not link:
                unpaired.append(planned)

        print(f"📅 Found {len(unpaired)} unpaired sessions out of {len(all_sessions)} total")

        paired_count = 0
        error_count = 0

        for planned in unpaired:
            print(f"\n🔍 Processing: {planned.title} on {planned.starts_at.date()}")
            try:
                try_auto_pair(planned=planned, session=session)
                session.commit()
                paired_count += 1
                print("   ✅ Pairing attempted")
            except Exception as e:
                session.rollback()
                error_count += 1
                print(f"   ❌ Error: {e}")

        print(f"\n📊 Summary: {paired_count} sessions processed, {error_count} errors")


def backfill_generation_for_all_paired(user_id: str) -> None:
    """Backfill workouts, executions, and compliance for all paired sessions."""
    with get_session() as db_session:
        all_sessions = db_session.scalars(
            select(PlannedSession)
            .where(PlannedSession.user_id == user_id)
            .order_by(PlannedSession.starts_at)
        ).all()

        # Filter to paired sessions
        paired_sessions = []
        for planned in all_sessions:
            link = get_link_for_planned(db_session, planned.id)
            if link:
                paired_sessions.append((planned, link))

        print(f"📅 Found {len(paired_sessions)} paired sessions out of {len(all_sessions)} total")

        backfilled_count = 0
        error_count = 0

        for planned, link in paired_sessions:
            print(f"\n🔍 Processing: {planned.title} on {planned.starts_at.date()}")
            try:
                activity = db_session.execute(
                    select(Activity).where(Activity.id == link.activity_id)
                ).scalar_one_or_none()

                if activity:
                    results = _ensure_generation_for_paired_session(db_session, planned, activity)
                    db_session.commit()
                    if any(results.values()):
                        backfilled_count += 1
                        print("   ✅ Backfill completed")
                    else:
                        print("   i  All generation data already exists")
                else:
                    print(f"   ⚠️  Activity {link.activity_id} not found")
            except Exception as e:
                db_session.rollback()
                error_count += 1
                print(f"   ❌ Error: {e}")

        print(f"\n📊 Summary: {backfilled_count} backfilled, {error_count} errors")


def main():
    parser = argparse.ArgumentParser(description="Manually trigger pairing for planned sessions")
    parser.add_argument("--user-id", required=True, help="User ID")
    parser.add_argument("--planned-session-id", help="Specific planned session ID to pair")
    parser.add_argument("--date-range", nargs=2, metavar=("START", "END"), help="Date range (YYYY-MM-DD)")
    parser.add_argument("--all-unpaired", action="store_true", help="Pair all unpaired sessions")
    parser.add_argument("--backfill-generation", action="store_true", help="Backfill generation for all paired sessions")
    parser.add_argument("--backfill", action="store_true", help="Backfill generation when using --date-range")

    args = parser.parse_args()

    if args.planned_session_id:
        pair_specific_session(args.planned_session_id, args.user_id)
    elif args.date_range:
        start_date = date.fromisoformat(args.date_range[0])
        end_date = date.fromisoformat(args.date_range[1])
        pair_sessions_in_date_range(args.user_id, start_date, end_date, backfill=args.backfill)
    elif args.all_unpaired:
        pair_all_unpaired(args.user_id)
    elif args.backfill_generation:
        backfill_generation_for_all_paired(args.user_id)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
