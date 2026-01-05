"""Helper functions for generating and storing planned training sessions."""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.state.db import get_session
from app.state.models import PlannedSession


def save_planned_sessions(
    user_id: str,
    athlete_id: int,
    sessions: list[dict],
    plan_type: str,
    plan_id: str | None = None,
) -> int:
    """Save planned training sessions to the database.

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID (Strava)
        sessions: List of session dictionaries with keys:
            - date: datetime or date string (YYYY-MM-DD)
            - time: Optional time string (HH:MM)
            - type: Activity type (Run, Bike, etc.)
            - title: Session title
            - duration_minutes: Optional duration
            - distance_km: Optional distance
            - intensity: Optional intensity (easy, moderate, hard, race)
            - notes: Optional notes
            - week_number: Optional week number in plan
        plan_type: Type of plan ("race" or "season")
        plan_id: Optional plan identifier

    Returns:
        Number of sessions saved
    """
    if not sessions:
        logger.warning("No sessions to save")
        return 0

    saved_count = 0
    with get_session() as session:
        for session_data in sessions:
            # Parse date
            if isinstance(session_data["date"], str):
                date_obj = datetime.strptime(session_data["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            elif isinstance(session_data["date"], datetime):
                date_obj = session_data["date"]
                if date_obj.tzinfo is None:
                    date_obj = date_obj.replace(tzinfo=timezone.utc)
            else:
                logger.error(f"Invalid date type: {type(session_data['date'])}")
                continue

            # Check if session already exists
            existing = session.execute(
                select(PlannedSession).where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.date == date_obj,
                    PlannedSession.title == session_data["title"],
                )
            ).first()

            if existing:
                logger.debug(f"Session already exists: {session_data['title']} on {date_obj.date()}")
                continue

            planned_session = PlannedSession(
                user_id=user_id,
                athlete_id=athlete_id,
                date=date_obj,
                time=session_data.get("time"),
                type=session_data["type"],
                title=session_data["title"],
                duration_minutes=session_data.get("duration_minutes"),
                distance_km=session_data.get("distance_km"),
                intensity=session_data.get("intensity"),
                notes=session_data.get("notes"),
                plan_type=plan_type,
                plan_id=plan_id,
                week_number=session_data.get("week_number"),
                status="planned",
            )

            session.add(planned_session)
            saved_count += 1

        session.commit()
        logger.info(f"Saved {saved_count} planned sessions for user_id={user_id}, plan_type={plan_type}")

    return saved_count


def _get_race_build_params(race_distance: str) -> tuple[int, str]:
    """Get weeks and focus for race distance."""
    distance_lower = race_distance.lower()
    if "5k" in distance_lower:
        return (12, "speed")
    if "10k" in distance_lower:
        return (14, "threshold")
    if "half" in distance_lower:
        return (16, "endurance")
    if "marathon" in distance_lower:
        return (20, "aerobic")
    if "ultra" in distance_lower or "100" in distance_lower:
        return (24, "durability")
    return (16, "general")


def _create_quality_workout(focus: str, quality_date: datetime, week_num: int) -> dict:
    """Create quality workout session based on focus."""
    workouts = {
        "speed": {
            "title": "Intervals",
            "duration_minutes": 45,
            "intensity": "hard",
            "notes": "5x800m at 5K pace with 2min recovery",
        },
        "threshold": {
            "title": "Threshold Run",
            "duration_minutes": 50,
            "intensity": "hard",
            "notes": "20min at threshold pace",
        },
        "endurance": {
            "title": "Tempo Run",
            "duration_minutes": 60,
            "intensity": "moderate",
            "notes": "30min at half marathon pace",
        },
        "aerobic": {
            "title": "Marathon Pace Run",
            "duration_minutes": 70 + (week_num * 5),
            "intensity": "moderate",
            "notes": f"{20 + (week_num * 2)}min at marathon pace",
        },
    }
    workout = workouts.get(
        focus,
        {
            "title": "Long Run",
            "duration_minutes": 90 + (week_num * 10),
            "intensity": "easy",
            "notes": "Time on feet focus",
        },
    )
    return {
        "date": quality_date,
        "type": "Run",
        "title": workout["title"],
        "duration_minutes": workout["duration_minutes"],
        "intensity": workout["intensity"],
        "notes": workout["notes"],
        "week_number": week_num,
    }


def generate_race_build_sessions(
    race_date: datetime,
    race_distance: str,
    target_time: str | None = None,  # noqa: ARG001
    start_date: datetime | None = None,
) -> list[dict]:
    """Generate training sessions for a race build.

    Args:
        race_date: Target race date
        race_distance: Race distance (5K, 10K, half, marathon, ultra)
        target_time: Optional target finish time (e.g., "3:30:00")
        start_date: Optional start date for training (defaults to 16 weeks before race)

    Returns:
        List of session dictionaries
    """
    weeks, focus = _get_race_build_params(race_distance)
    if start_date is None:
        start_date = race_date - timedelta(weeks=weeks)
    else:
        # Adjust start_date to match weeks if provided
        start_date = race_date - timedelta(weeks=weeks)

    sessions = []
    current_date = start_date

    # Generate weekly structure
    distance_lower = race_distance.lower()
    for week_num in range(1, weeks + 1):
        if week_num % 4 != 0:
            sessions.append({
                "date": current_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 30 + (week_num * 5),
                "intensity": "easy",
                "week_number": week_num,
            })

        quality_date = current_date + timedelta(days=2)
        sessions.append(_create_quality_workout(focus, quality_date, week_num))

        # Saturday: Long run (for distances half marathon and up)
        if "half" in distance_lower or "marathon" in distance_lower or "ultra" in distance_lower:
            long_run_date = current_date + timedelta(days=5)
            long_run_minutes = 60 if "half" in distance_lower else (90 if "marathon" in distance_lower else 120)
            long_run_minutes += week_num * 5

            sessions.append({
                "date": long_run_date,
                "type": "Run",
                "title": "Long Run",
                "duration_minutes": min(long_run_minutes, 180 if "ultra" in distance_lower else 150),
                "intensity": "easy",
                "notes": "Aerobic base building",
                "week_number": week_num,
            })

        current_date += timedelta(days=7)

    return sessions


def generate_season_sessions(
    season_start: datetime,
    season_end: datetime,
    target_races: list[dict] | None = None,  # noqa: ARG001
) -> list[dict]:
    """Generate training sessions for a season plan.

    Args:
        season_start: Season start date
        season_end: Season end date
        target_races: Optional list of race dictionaries with keys:
            - date: race date
            - distance: race distance
            - target_time: optional target time

    Returns:
        List of session dictionaries
    """
    sessions = []
    current_date = season_start
    week_num = 1

    # Calculate total weeks
    total_weeks = (season_end - season_start).days // 7

    # Phase structure: Base (40%), Build (35%), Peak (15%), Recovery (10%)
    base_weeks = int(total_weeks * 0.4)
    build_weeks = int(total_weeks * 0.35)
    peak_weeks = int(total_weeks * 0.15)
    recovery_weeks = total_weeks - base_weeks - build_weeks - peak_weeks

    phase = "base"
    phase_week = 0

    while current_date < season_end:
        phase_week += 1

        if phase == "base":
            if phase_week > base_weeks:
                phase = "build"
                phase_week = 0
                continue
            # Base phase: Easy runs, aerobic volume
            sessions.append({
                "date": current_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 45,
                "intensity": "easy",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=2),
                "type": "Run",
                "title": "Aerobic Run",
                "duration_minutes": 60,
                "intensity": "easy",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=4),
                "type": "Run",
                "title": "Long Run",
                "duration_minutes": 90,
                "intensity": "easy",
                "week_number": week_num,
            })

        elif phase == "build":
            if phase_week > build_weeks:
                phase = "peak"
                phase_week = 0
                continue
            # Build phase: Add quality workouts
            sessions.append({
                "date": current_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 40,
                "intensity": "easy",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=2),
                "type": "Run",
                "title": "Tempo Run",
                "duration_minutes": 50,
                "intensity": "moderate",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=4),
                "type": "Run",
                "title": "Long Run",
                "duration_minutes": 100,
                "intensity": "easy",
                "week_number": week_num,
            })

        elif phase == "peak":
            if phase_week > peak_weeks:
                phase = "recovery"
                phase_week = 0
                continue
            # Peak phase: Race-specific work
            sessions.append({
                "date": current_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 30,
                "intensity": "easy",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=2),
                "type": "Run",
                "title": "Race Pace Workout",
                "duration_minutes": 45,
                "intensity": "hard",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=5),
                "type": "Run",
                "title": "Long Run",
                "duration_minutes": 90,
                "intensity": "moderate",
                "week_number": week_num,
            })

        else:  # recovery
            if phase_week > recovery_weeks:
                break
            # Recovery phase: Easy volume
            sessions.append({
                "date": current_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 30,
                "intensity": "easy",
                "week_number": week_num,
            })
            sessions.append({
                "date": current_date + timedelta(days=3),
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": 40,
                "intensity": "easy",
                "week_number": week_num,
            })

        current_date += timedelta(days=7)
        week_num += 1

    return sessions
