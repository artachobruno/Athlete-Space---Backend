"""Training plan service - orchestration layer for training plan generation.

This service provides the entry point for training plan generation.
It handles:
- Input validation
- Calling domain pipeline
- Persistence (via planner)
- Event emission

The domain layer (app.domains.training_plan) contains pure business logic
and does NOT handle persistence or external side effects.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.planner.plan_race_simple import plan_race_simple

VALID_DISTANCES = {"5K", "10K", "Half Marathon", "Marathon", "Ultra"}

DISTANCE_ALIASES = {
    "5k": "5K",
    "10k": "10K",
    "half": "Half Marathon",
    "half marathon": "Half Marathon",
    "half-marathon": "Half Marathon",
    "halfmarathon": "Half Marathon",
    "marathon": "Marathon",
    "full": "Marathon",
    "full marathon": "Marathon",
    "full-marathon": "Marathon",
    "fullmarathon": "Marathon",
    "ultra": "Ultra",
    "ultramarathon": "Ultra",
    "ultra marathon": "Ultra",
    "ultra-marathon": "Ultra",
}


def normalize_distance(distance: str) -> str:
    """Normalize distance string to valid format.

    Accepts various formats (lowercase, with/without spaces, etc.) and
    converts them to the canonical format expected by plan_race.

    Args:
        distance: Distance string in any format

    Returns:
        Normalized distance string (one of VALID_DISTANCES)
    """
    if not distance:
        return distance

    d = distance.strip()

    # If already in valid format, return as-is
    if d in VALID_DISTANCES:
        return d

    # Try to normalize via aliases
    key = d.lower()
    normalized = DISTANCE_ALIASES.get(key)
    if normalized:
        return normalized

    # Return original if no match (will fail validation later)
    return d


async def plan_race(
    race_date: datetime,
    distance: str,
    user_id: str,
    athlete_id: int,
    *,
    start_date: datetime | None = None,
    athlete_state: AthleteState | None = None,
    progress_callback: Callable[[int, int, str], Awaitable[None] | None] | None = None,
    race_priority: str | None = None,
) -> tuple[list[dict], int]:
    """Generate and persist a race training plan.

    This is the service layer entry point for race plan generation.
    It validates inputs, calls the domain pipeline, and handles persistence.

    Args:
        race_date: Race date
        distance: Race distance ("5K", "10K", "Half Marathon", "Marathon", "Ultra")
        user_id: User ID (required)
        athlete_id: Athlete ID (required)
        start_date: Training start date (optional, defaults to 16 weeks before race)
        athlete_state: Athlete state snapshot (optional, will use defaults if None)
        progress_callback: Optional callback(week_number, total_weeks, phase) for progress tracking
        race_priority: Optional race priority (A/B/C) for taper logic adjustment

    Returns:
        Tuple of (list of session dictionaries, total weeks)

    Raises:
        ValueError: If input validation fails
        RuntimeError: If planning fails at any stage
    """
    # Input validation
    if not user_id:
        raise ValueError("user_id is required")
    if not athlete_id:
        raise ValueError("athlete_id is required")
    if not distance:
        raise ValueError("distance is required")
    if not race_date:
        raise ValueError("race_date is required")

    # Normalize distance (e.g., "marathon" -> "Marathon")
    distance = normalize_distance(distance)

    if distance not in VALID_DISTANCES:
        raise ValueError(f"Invalid distance: {distance}. Must be one of {VALID_DISTANCES}")

    if race_date < datetime.now(race_date.tzinfo):
        raise ValueError("race_date must be in the future")

    logger.info(
        "Training plan service: Starting race plan generation",
        distance=distance,
        race_date=race_date.isoformat(),
        user_id=user_id,
        athlete_id=athlete_id,
    )

    try:
        # Call domain pipeline (via planner, which handles persistence)
        sessions, total_weeks = await plan_race_simple(
            race_date=race_date,
            distance=distance,
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            athlete_state=athlete_state,
            progress_callback=progress_callback,
            race_priority=race_priority,
        )

        logger.info(
            "Training plan service: Race plan generation complete",
            distance=distance,
            total_weeks=total_weeks,
            total_sessions=len(sessions),
            user_id=user_id,
            athlete_id=athlete_id,
        )
    except Exception as e:
        logger.error(
            "Training plan service: Race plan generation failed",
            distance=distance,
            race_date=race_date.isoformat(),
            user_id=user_id,
            athlete_id=athlete_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise
    else:
        return sessions, total_weeks
