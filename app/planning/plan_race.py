from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.planning.llm.plan_session import plan_session_llm
from app.planning.llm.plan_week import PlanWeekInput, plan_week_llm
from app.planning.schema.session_output import SessionPlan
from app.planning.schema.session_spec import SessionSpec, Sport


def compute_phase(week_number: int, total_weeks: int) -> str:
    """Compute training phase from week number.

    Args:
        week_number: Current week number (1-based)
        total_weeks: Total weeks in plan

    Returns:
        Phase string: "base" | "build" | "peak" | "taper"
    """
    if week_number <= total_weeks * 0.5:
        return "base"
    if week_number <= total_weeks * 0.8:
        return "build"
    if week_number <= total_weeks * 0.9:
        return "peak"
    return "taper"


def calculate_weekly_volumes(
    distance: str,
    total_weeks: int,
) -> list[dict[str, float]]:
    """Calculate weekly volumes for a race plan.

    Args:
        distance: Race distance ("5K", "10K", "Half Marathon", "Marathon", "Ultra")
        total_weeks: Total number of weeks

    Returns:
        List of dictionaries with "total" and "long" keys for each week
    """
    base_volumes = {
        "5K": {"base": 25, "peak": 35, "long": 8},
        "10K": {"base": 35, "peak": 50, "long": 12},
        "Half Marathon": {"base": 40, "peak": 65, "long": 21},
        "Marathon": {"base": 50, "peak": 80, "long": 32},
        "Ultra": {"base": 60, "peak": 100, "long": 42},
    }

    volumes_config = base_volumes.get(distance, base_volumes["Marathon"])
    base_volume = volumes_config["base"]
    peak_volume = volumes_config["peak"]
    long_run_base = volumes_config["long"]

    weekly_volumes = []
    for week_num in range(1, total_weeks + 1):
        progress = week_num / total_weeks

        if progress <= 0.5:
            volume = base_volume + (peak_volume - base_volume) * progress * 2
        elif progress <= 0.8:
            volume = base_volume + (peak_volume - base_volume) * (0.5 + (progress - 0.5) * 2)
        elif progress <= 0.9:
            volume = peak_volume
        else:
            taper_ratio = (1.0 - progress) / 0.1
            volume = peak_volume * (0.5 + taper_ratio * 0.3)

        long_run = long_run_base * (0.7 + progress * 0.3) if progress < 0.9 else long_run_base * 0.5

        weekly_volumes.append({"total": round(volume, 1), "long": round(long_run, 1)})

    return weekly_volumes


def session_plan_to_dict(
    session_plan: SessionPlan,
    spec: SessionSpec,
    session_date: datetime,
) -> dict:
    """Convert SessionPlan and SessionSpec to session dictionary for saving.

    Args:
        session_plan: Generated session plan from LLM
        spec: Original session specification
        session_date: Date for this session

    Returns:
        Dictionary compatible with save_planned_sessions
    """
    total_distance = 0.0
    total_duration = 0

    for block in session_plan.structure:
        block_distance = block.distance_km or 0.0
        block_duration = block.duration_min or 0

        if block.reps and block.reps > 1:
            block_distance *= block.reps
            block_duration *= block.reps
            if block.float_km:
                total_distance += block.float_km * (block.reps - 1)

        total_distance += block_distance
        total_duration += block_duration

    session_dict: dict = {
        "date": session_date,
        "type": spec.sport.value.capitalize(),
        "title": session_plan.title,
        "description": session_plan.notes or "",
        "distance_km": total_distance if total_distance > 0 else spec.target_distance_km,
        "duration_minutes": total_duration if total_duration > 0 else spec.target_duration_min,
        "intensity": spec.intensity.value,
        "notes": session_plan.notes,
        "week_number": spec.week_number,
    }

    return session_dict


async def plan_race_build_new(
    race_date: datetime,
    distance: str,
    user_id: str,
    athlete_id: int,
    *,
    start_date: datetime | None = None,
    progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> tuple[list[dict], int]:
    """Generate race plan using hierarchical, compositional approach.

    Args:
        race_date: Race date
        distance: Race distance
        start_date: Training start date (optional, defaults to 16 weeks before race)
        user_id: User ID
        athlete_id: Athlete ID
        progress_callback: Optional async callback function(week_number, total_weeks, phase) for progress tracking

    Returns:
        Tuple of (list of session dictionaries, total weeks)
    """
    if start_date is None:
        start_date = race_date - timedelta(weeks=16)

    total_weeks = int((race_date.date() - start_date.date()).days / 7)
    if total_weeks < 4:
        total_weeks = 16
        start_date = race_date - timedelta(weeks=16)

    weekly_volumes = calculate_weekly_volumes(distance, total_weeks)

    sport = Sport.RUN

    all_sessions = []

    for week_number in range(1, total_weeks + 1):
        phase = compute_phase(week_number, total_weeks)
        volume = weekly_volumes[week_number - 1]

        # Emit progress event for this week
        if progress_callback:
            await progress_callback(week_number, total_weeks, phase)

        logger.info(
            "plan_race_build_new: Planning week",
            week_number=week_number,
            total_weeks=total_weeks,
            phase=phase,
            percentage=round((week_number / total_weeks) * 100, 1),
            total_volume_km=volume["total"],
            long_run_km=volume["long"],
        )

        week_start = start_date + timedelta(weeks=week_number - 1)
        monday = week_start - timedelta(days=week_start.weekday())

        week_input = PlanWeekInput(
            week_number=week_number,
            phase=phase,
            total_volume_km=volume["total"],
            long_run_km=volume["long"],
            days_available=[0, 1, 2, 3, 4, 5, 6],
            sport=sport,
            athlete_context=None,
        )

        week_specs = await plan_week_llm(week_input)

        for spec in week_specs:
            session_date = monday + timedelta(days=spec.day_of_week)
            session_date = datetime.combine(
                session_date.date(),
                datetime.min.time(),
            ).replace(tzinfo=timezone.utc)

            session_plan = await plan_session_llm(spec)
            session_dict = session_plan_to_dict(session_plan, spec, session_date)
            all_sessions.append(session_dict)

    logger.info(
        "plan_race_build_new: Generated race plan",
        distance=distance,
        race_date=race_date.isoformat(),
        start_date=start_date.isoformat(),
        total_weeks=total_weeks,
        total_sessions=len(all_sessions),
        user_id=user_id,
        athlete_id=athlete_id,
    )

    return all_sessions, total_weeks
