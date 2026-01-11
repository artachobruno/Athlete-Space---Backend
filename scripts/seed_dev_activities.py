"""Seed realistic fake activities for dev user.

Creates 10 weeks of realistic training data:
- Tuesday/Thursday workouts (tempo, intervals)
- Saturday long runs
- Mix of easy runs
- Realistic paces, durations, distances
"""

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from sqlalchemy import select

from app.db.models import Activity, User
from app.db.session import get_session

# Dev user email
USER_EMAIL = "athlete_test@example.com"


def seed_activities() -> int:
    """Seed realistic activities for the dev user.

    Returns:
        Number of activities created
    """
    with get_session() as db:
        # Find dev user
        user_result = db.execute(select(User).where(User.email == USER_EMAIL)).first()
        if not user_result:
            raise RuntimeError(f"User not found: {USER_EMAIL}. Run scripts/create_dev_user.py first.")

        user = user_result[0]
        user_id = user.id

        # Check if activities already exist
        existing_activities = db.execute(
            select(Activity).where(Activity.user_id == user_id)
        ).scalars().all()

        existing_count = len(existing_activities)
        if existing_count > 0:
            print(f"⚠️  Activities already exist for this user ({existing_count} activities)")
            response = input("Delete existing activities and reseed? (y/N): ").strip().lower()
            if response != "y":
                print("Aborted.")
                return existing_count

            # Delete existing activities
            for activity in existing_activities:
                db.delete(activity)
            db.commit()
            print(f"Deleted {existing_count} existing activities")

        # Generate 10 weeks of data
        now = datetime.now(UTC)
        start_date = now - timedelta(weeks=10)
        # Start on a Monday
        days_since_monday = start_date.weekday()
        start_date -= timedelta(days=days_since_monday)

        activities = []
        activity_counter = 1  # For unique strava_activity_id

        for week in range(10):
            base = start_date + timedelta(weeks=week)

            # Monday: Easy run
            activities.append(
                Activity(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    strava_activity_id=f"dev_{activity_counter}",
                    athlete_id="dev_athlete",  # Placeholder for dev activities
                    type="Run",
                    start_time=base + timedelta(days=0, hours=6),
                    duration_seconds=1800,  # 30 min
                    distance_meters=5000,  # 5k
                    elevation_gain_meters=50,
                    source="dev_seed",
                    created_at=datetime.now(UTC),
                )
            )
            activity_counter += 1

            # Tuesday: Workout (tempo/intervals)
            activities.append(
                Activity(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    strava_activity_id=f"dev_{activity_counter}",
                    athlete_id="dev_athlete",
                    type="Run",
                    start_time=base + timedelta(days=1, hours=6),
                    duration_seconds=3600,  # 60 min
                    distance_meters=12000,  # 12k
                    elevation_gain_meters=100,
                    source="dev_seed",
                    created_at=datetime.now(UTC),
                )
            )
            activity_counter += 1

            # Wednesday: Rest or easy (50% chance)
            if week % 2 == 0:
                activities.append(
                    Activity(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        strava_activity_id=f"dev_{activity_counter}",
                        athlete_id="dev_athlete",
                        type="Run",
                        start_time=base + timedelta(days=2, hours=6),
                        duration_seconds=1500,  # 25 min
                        distance_meters=4000,  # 4k
                        elevation_gain_meters=30,
                        source="dev_seed",
                        created_at=datetime.now(UTC),
                    )
                )
                activity_counter += 1

            # Thursday: Workout (intervals)
            activities.append(
                Activity(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    strava_activity_id=f"dev_{activity_counter}",
                    athlete_id="dev_athlete",
                    type="Run",
                    start_time=base + timedelta(days=3, hours=6),
                    duration_seconds=3800,  # ~63 min
                    distance_meters=13000,  # 13k
                    elevation_gain_meters=120,
                    source="dev_seed",
                    created_at=datetime.now(UTC),
                )
            )
            activity_counter += 1

            # Friday: Easy
            activities.append(
                Activity(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    strava_activity_id=f"dev_{activity_counter}",
                    athlete_id="dev_athlete",
                    type="Run",
                    start_time=base + timedelta(days=4, hours=6),
                    duration_seconds=1800,  # 30 min
                    distance_meters=5000,  # 5k
                    elevation_gain_meters=40,
                    source="dev_seed",
                    created_at=datetime.now(UTC),
                )
            )
            activity_counter += 1

            # Saturday: Long run
            activities.append(
                Activity(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    strava_activity_id=f"dev_{activity_counter}",
                    athlete_id="dev_athlete",
                    type="Run",
                    start_time=base + timedelta(days=5, hours=7),
                    duration_seconds=7200,  # 2 hours
                    distance_meters=24000,  # 24k
                    elevation_gain_meters=200,
                    source="dev_seed",
                    created_at=datetime.now(UTC),
                )
            )
            activity_counter += 1

            # Sunday: Rest (skip)

        # Add all activities
        db.add_all(activities)
        db.commit()

        print(f"✅ Seeded {len(activities)} activities for user: {user_id}")
        print(f"   Date range: {start_date.date()} to {base + timedelta(days=5)}")
        return len(activities)


if __name__ == "__main__":
    try:
        count = seed_activities()
        print(f"\n✅ Success! Seeded {count} activities")
        print("\nThe dev user now has realistic training data that the coach can use.")
    except Exception as e:
        print(f"❌ Error seeding activities: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
