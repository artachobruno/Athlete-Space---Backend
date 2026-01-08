"""Helper functions for generating and storing planned training sessions."""

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.intent_schemas import SeasonPlan, WeeklyIntent


async def save_planned_sessions(
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

    # Convert datetime objects to ISO strings for MCP
    sessions_for_mcp = []
    for session_data in sessions:
        mcp_session = session_data.copy()
        # Convert date to ISO string if it's a datetime
        session_date = mcp_session.get("date")
        if isinstance(session_date, (datetime, date)):
            mcp_session["date"] = session_date.isoformat()
        sessions_for_mcp.append(mcp_session)

    try:
        result = await call_tool(
            "save_planned_sessions",
            {
                "user_id": user_id,
                "athlete_id": athlete_id,
                "sessions": sessions_for_mcp,
                "plan_type": plan_type,
                "plan_id": plan_id,
            },
        )
        saved_count = result.get("saved_count", 0)
        logger.info(f"Saved {saved_count} planned sessions via MCP for user_id={user_id}, plan_type={plan_type}")
    except MCPError as e:
        logger.error(f"Failed to save planned sessions via MCP: {e.code}: {e.message}")
        raise RuntimeError(f"Failed to save planned sessions: {e.message}") from e
    else:
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
    _target_time: str | None = None,
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
    _target_races: list[dict] | None = None,
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


def weekly_intent_to_sessions(weekly_intent: WeeklyIntent) -> list[dict]:
    """Convert WeeklyIntent to planned sessions for the week.

    Args:
        weekly_intent: WeeklyIntent object

    Returns:
        List of session dictionaries for the week
    """
    sessions = []
    week_start = weekly_intent.week_start

    # Convert date to datetime for calculations
    if isinstance(week_start, date):
        week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    else:
        week_start_dt = week_start

    # Parse intensity distribution to determine session types
    intensity_lower = weekly_intent.intensity_distribution.lower()
    volume_hours = weekly_intent.volume_target_hours

    # Determine number of sessions based on intensity distribution
    hard_count = 0
    moderate_count = 0
    easy_count = 0

    if "hard" in intensity_lower or "intensity" in intensity_lower:
        # Count hard sessions
        for word in intensity_lower.split():
            if word.isdigit():
                hard_count = int(word)
                break
        if hard_count == 0:
            hard_count = 1 if "hard" in intensity_lower else 0

    if "moderate" in intensity_lower or "tempo" in intensity_lower:
        moderate_count = 2

    if "easy" in intensity_lower or "aerobic" in intensity_lower:
        easy_count = 4

    # Default distribution if parsing fails
    if hard_count == 0 and moderate_count == 0 and easy_count == 0:
        # Default: 1-2 quality sessions, rest easy
        hard_count = 1 if volume_hours > 8 else 0
        moderate_count = 1 if volume_hours > 10 else 0
        easy_count = max(3, int(volume_hours / 1.5))  # ~1.5 hours per easy session

    # Calculate session durations
    total_sessions = hard_count + moderate_count + easy_count
    if total_sessions == 0:
        return sessions

    # Distribute volume across sessions
    # Hard sessions: ~1 hour, Moderate: ~1.5 hours, Easy: remaining volume
    hard_duration = 60  # minutes
    moderate_duration = 90  # minutes
    remaining_hours = volume_hours - (hard_count * 1.0) - (moderate_count * 1.5)
    easy_duration = int((remaining_hours * 60) / max(easy_count, 1)) if easy_count > 0 else 0
    easy_duration = max(30, min(easy_duration, 120))  # Clamp between 30-120 min

    # Distribute sessions across the week (Monday=0, Sunday=6)
    # Use a smarter distribution: hard on Tue/Thu, moderate on Wed, easy on other days
    used_days = set()

    # Add hard sessions (typically Tuesday/Thursday)
    hard_days = [1, 3]  # Tuesday, Thursday
    for i, _ in enumerate(range(hard_count)):
        if i < len(hard_days):
            day = hard_days[i]
            used_days.add(day)
            session_date = week_start_dt + timedelta(days=day)
            sessions.append({
                "date": session_date,
                "type": "Run",
                "title": "Hard Workout",
                "duration_minutes": hard_duration,
                "intensity": "hard",
                "notes": weekly_intent.focus,
                "week_number": weekly_intent.week_number,
            })

    # Add moderate sessions (typically Wednesday)
    moderate_days = [2]  # Wednesday
    for i, _ in enumerate(range(moderate_count)):
        if i < len(moderate_days):
            day = moderate_days[i]
            used_days.add(day)
            session_date = week_start_dt + timedelta(days=day)
            sessions.append({
                "date": session_date,
                "type": "Run",
                "title": "Moderate Run",
                "duration_minutes": moderate_duration,
                "intensity": "moderate",
                "notes": weekly_intent.adaptation_goal,
                "week_number": weekly_intent.week_number,
            })

    # Add easy sessions (fill remaining days, prefer Mon/Fri/Sat)
    easy_days = [0, 4, 5, 6]  # Monday, Friday, Saturday, Sunday
    easy_day_idx = 0
    for _ in range(easy_count):
        # Find next available day
        while easy_day_idx < len(easy_days) and easy_days[easy_day_idx] in used_days:
            easy_day_idx += 1
        if easy_day_idx < len(easy_days):
            day = easy_days[easy_day_idx]
            used_days.add(day)
            session_date = week_start_dt + timedelta(days=day)
            sessions.append({
                "date": session_date,
                "type": "Run",
                "title": "Easy Run",
                "duration_minutes": easy_duration,
                "intensity": "easy",
                "notes": weekly_intent.adaptation_goal,
                "week_number": weekly_intent.week_number,
            })
            easy_day_idx += 1
        else:
            # If we run out of preferred days, use any available day
            for d in range(7):
                if d not in used_days:
                    used_days.add(d)
                    session_date = week_start_dt + timedelta(days=d)
                    sessions.append({
                        "date": session_date,
                        "type": "Run",
                        "title": "Easy Run",
                        "duration_minutes": easy_duration,
                        "intensity": "easy",
                        "notes": weekly_intent.adaptation_goal,
                        "week_number": weekly_intent.week_number,
                    })
                    break

    return sessions


def season_plan_to_sessions(season_plan: SeasonPlan) -> list[dict]:
    """Convert SeasonPlan to planned sessions.

    Args:
        season_plan: SeasonPlan object

    Returns:
        List of session dictionaries for the season
    """
    # Convert dates to datetime
    if isinstance(season_plan.season_start, date):
        season_start = datetime.combine(season_plan.season_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    else:
        season_start = season_plan.season_start

    if isinstance(season_plan.season_end, date):
        season_end = datetime.combine(season_plan.season_end, datetime.min.time()).replace(tzinfo=timezone.utc)
    else:
        season_end = season_plan.season_end

    # Use existing generate_season_sessions function
    return generate_season_sessions(
        season_start=season_start,
        season_end=season_end,
        _target_races=None,  # Could be enhanced to use season_plan.target_races
    )
