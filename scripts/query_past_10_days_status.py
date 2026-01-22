"""Query activities and planned sessions from past 10 days with pairing status.

This script queries the database to show:
- All activities from the past 10 days (completed)
- All planned sessions from the past 10 days (planned/completed)
- Pairing status for each
- Whether activities are "paired" (multiple activities on same day)
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession, SessionLink, User
from app.db.session import get_session
from app.pairing.session_links import get_link_for_activity, get_link_for_planned


def get_user_id_from_email(session: Session, email: str | None = None) -> str | None:
    """Get user_id from email, or return first user if email is None."""
    if email:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user:
            return user.id
        return None
    
    # Get first user
    user = session.execute(select(User).limit(1)).scalar_one_or_none()
    if user:
        return user.id
    return None


def format_duration_minutes(duration_seconds: int | None) -> str:
    """Format duration in seconds to human-readable string."""
    if duration_seconds is None:
        return "N/A"
    minutes = duration_seconds // 60
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def format_distance_km(distance_meters: float | None) -> str:
    """Format distance in meters to km string."""
    if distance_meters is None:
        return "N/A"
    return f"{distance_meters / 1000.0:.1f} km"


def query_past_10_days_status(user_id: str | None = None, user_email: str | None = None) -> None:
    """Query and display activities and planned sessions from past 10 days.
    
    Args:
        user_id: Optional user ID to query. If None, uses first user or user_email.
        user_email: Optional user email to query. If user_id is None, uses this.
    """
    with get_session() as session:
        # Get user_id
        if not user_id:
            user_id = get_user_id_from_email(session, user_email)
        
        if not user_id:
            print("ERROR: No user found. Please provide user_id or user_email.")
            sys.exit(1)
        
        print(f"Querying data for user_id: {user_id}\n")
        
        # Calculate date range (past 10 days, including today)
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=9)  # 10 days total (0-9 days ago)
        end_date = today
        
        start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)
        
        print(f"Date range: {start_date} to {end_date} (past 10 days)\n")
        print("=" * 80)
        print()
        
        # Query activities
        activities_query = (
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.starts_at >= start_datetime,
                Activity.starts_at <= end_datetime,
            )
            .order_by(Activity.starts_at.asc())
        )
        activities = list(session.scalars(activities_query).all())
        
        # Query planned sessions
        planned_sessions_query = (
            select(PlannedSession)
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.starts_at >= start_datetime,
                PlannedSession.starts_at <= end_datetime,
            )
            .order_by(PlannedSession.starts_at.asc())
        )
        planned_sessions = list(session.scalars(planned_sessions_query).all())
        
        # Build pairing map: activity_id -> planned_session_id
        pairing_map: dict[str, str] = {}
        planned_to_activity: dict[str, str] = {}
        
        for activity in activities:
            link = get_link_for_activity(session, activity.id)
            if link:
                pairing_map[activity.id] = link.planned_session_id
                planned_to_activity[link.planned_session_id] = activity.id
        
        for planned in planned_sessions:
            link = get_link_for_planned(session, planned.id)
            if link and link.activity_id:
                pairing_map[link.activity_id] = planned.id
                planned_to_activity[planned.id] = link.activity_id
        
        # Group by date
        activities_by_date: dict[date, list[Activity]] = {}
        planned_by_date: dict[date, list[PlannedSession]] = {}
        
        for activity in activities:
            activity_date = activity.starts_at.date()
            if activity_date not in activities_by_date:
                activities_by_date[activity_date] = []
            activities_by_date[activity_date].append(activity)
        
        for planned in planned_sessions:
            planned_date = planned.starts_at.date()
            if planned_date not in planned_by_date:
                planned_by_date[planned_date] = []
            planned_by_date[planned_date].append(planned)
        
        # Display results by date
        current_date = start_date
        while current_date <= end_date:
            print(f"\n📅 {current_date.strftime('%A, %B %d, %Y')}")
            print("-" * 80)
            
            day_activities = activities_by_date.get(current_date, [])
            day_planned = planned_by_date.get(current_date, [])
            
            # Show activities (completed)
            if day_activities:
                print(f"\n✅ COMPLETED ACTIVITIES ({len(day_activities)}):")
                for idx, activity in enumerate(day_activities, 1):
                    is_paired = activity.id in pairing_map
                    paired_with = pairing_map.get(activity.id, None)
                    
                    status = "PAIRED" if is_paired else "UNPAIRED"
                    pairing_info = f" → Paired with planned session {paired_with[:8]}..." if is_paired else ""
                    
                    print(f"  {idx}. {activity.title or activity.sport.upper()}")
                    print(f"     Status: {status}{pairing_info}")
                    print(f"     Duration: {format_duration_minutes(activity.duration_seconds)}")
                    print(f"     Distance: {format_distance_km(activity.distance_meters)}")
                    print(f"     Sport: {activity.sport}")
                    print(f"     Activity ID: {activity.id[:8]}...")
                    print()
            else:
                print("\n✅ COMPLETED ACTIVITIES: None")
            
            # Show planned sessions
            if day_planned:
                print(f"\n📋 PLANNED SESSIONS ({len(day_planned)}):")
                for idx, planned in enumerate(day_planned, 1):
                    is_paired = planned.id in planned_to_activity
                    paired_with = planned_to_activity.get(planned.id, None)
                    
                    # Determine status
                    if planned.status == "completed":
                        status_display = "COMPLETED"
                    elif planned.status == "skipped":
                        status_display = "SKIPPED"
                    elif planned.status == "deleted":
                        status_display = "DELETED"
                    else:
                        status_display = "PLANNED"
                    
                    pairing_info = f" → Paired with activity {paired_with[:8]}..." if is_paired else ""
                    pairing_status = "PAIRED" if is_paired else "UNPAIRED"
                    
                    print(f"  {idx}. {planned.title or planned.type.upper()}")
                    print(f"     Status: {status_display}")
                    print(f"     Pairing: {pairing_status}{pairing_info}")
                    print(f"     Duration: {format_duration_minutes(planned.duration_seconds)}")
                    print(f"     Distance: {format_distance_km(planned.distance_meters)}")
                    print(f"     Type: {planned.type}")
                    print(f"     Intensity: {planned.intensity or 'N/A'}")
                    print(f"     Planned Session ID: {planned.id[:8]}...")
                    print()
            else:
                print("\n📋 PLANNED SESSIONS: None")
            
            # Show pairing summary for the day
            total_items = len(day_activities) + len(day_planned)
            paired_count = sum(1 for a in day_activities if a.id in pairing_map)
            paired_count += sum(1 for p in day_planned if p.id in planned_to_activity)
            
            if total_items > 0:
                is_paired_day = len(day_activities) > 1 or (len(day_activities) > 0 and len(day_planned) > 0)
                pairing_summary = "PAIRED DAY" if is_paired_day else "SINGLE ACTIVITY DAY"
                print(f"\n📊 Day Summary: {total_items} total items, {paired_count} paired, {pairing_summary}")
            
            current_date += timedelta(days=1)
        
        # Overall summary
        print("\n" + "=" * 80)
        print("\n📊 OVERALL SUMMARY (Past 10 Days)")
        print("-" * 80)
        print(f"Total Activities: {len(activities)}")
        print(f"Total Planned Sessions: {len(planned_sessions)}")
        print(f"Paired Activities: {len(pairing_map)}")
        print(f"Paired Planned Sessions: {len(planned_to_activity)}")
        
        # Count paired vs unpaired
        unpaired_activities = len(activities) - len(pairing_map)
        unpaired_planned = len([p for p in planned_sessions if p.id not in planned_to_activity and p.status not in ["deleted", "skipped"]])
        
        print(f"\nUnpaired Activities: {unpaired_activities}")
        print(f"Unpaired Planned Sessions: {unpaired_planned}")
        
        # Count days with multiple activities (paired days)
        paired_days = sum(
            1 for date_key in activities_by_date.keys()
            if len(activities_by_date[date_key]) > 1
        )
        print(f"\nDays with Multiple Activities: {paired_days}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Query activities and planned sessions from past 10 days")
    parser.add_argument("--user-id", type=str, help="User ID to query")
    parser.add_argument("--user-email", type=str, help="User email to query")
    
    args = parser.parse_args()
    
    query_past_10_days_status(user_id=args.user_id, user_email=args.user_email)
