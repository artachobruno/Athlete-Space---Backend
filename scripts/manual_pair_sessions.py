"""Script to manually trigger pairing for planned sessions and activities.

This script can be used to:
1. Pair specific planned sessions with activities
2. Re-run auto-pairing for sessions that should be paired
3. Debug pairing issues

Usage:
    python scripts/manual_pair_sessions.py --user-id <user_id> --planned-session-id <id>
    python scripts/manual_pair_sessions.py --user-id <user_id> --date-range 2026-01-01 2026-01-31
    python scripts/manual_pair_sessions.py --user-id <user_id> --all-unpaired
"""

import argparse
import sys
from datetime import UTC, date, datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.db.models import PlannedSession
from app.db.session import get_session
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_planned


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


def pair_sessions_in_date_range(user_id: str, start_date: date, end_date: date) -> None:
    """Pair all unpaired sessions in a date range."""
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC)
    end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC)

    with get_session() as session:
        sessions = session.scalars(
            select(PlannedSession)
            .where(PlannedSession.user_id == user_id)
            .where(PlannedSession.starts_at >= start_datetime)
            .where(PlannedSession.starts_at <= end_datetime)
            .order_by(PlannedSession.starts_at)
        ).all()

        print(f"📅 Found {len(sessions)} planned sessions in range {start_date} to {end_date}")

        paired_count = 0
        error_count = 0

        for planned in sessions:
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


def main():
    parser = argparse.ArgumentParser(description="Manually trigger pairing for planned sessions")
    parser.add_argument("--user-id", required=True, help="User ID")
    parser.add_argument("--planned-session-id", help="Specific planned session ID to pair")
    parser.add_argument("--date-range", nargs=2, metavar=("START", "END"), help="Date range (YYYY-MM-DD)")
    parser.add_argument("--all-unpaired", action="store_true", help="Pair all unpaired sessions")

    args = parser.parse_args()

    if args.planned_session_id:
        pair_specific_session(args.planned_session_id, args.user_id)
    elif args.date_range:
        start_date = date.fromisoformat(args.date_range[0])
        end_date = date.fromisoformat(args.date_range[1])
        pair_sessions_in_date_range(args.user_id, start_date, end_date)
    elif args.all_unpaired:
        pair_all_unpaired(args.user_id)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
