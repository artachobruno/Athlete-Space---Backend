from typing import cast

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.planning.repair.volume_repair import RepairImpossibleError, repair_week_volume, volume_within_tolerance
from app.planning.schema.session_spec import SessionSpec, Sport
from app.services.llm.model import get_model

SYSTEM_PROMPT = """You are an elite endurance coach.

Your task is to design ONE training week.

Rules:
- You must respect the total weekly volume.
- You must include exactly one long run.
- You must not schedule more than 2 hard sessions.
- You must space hard sessions by at least 48 hours.
- You must output ONLY SessionSpec objects.
- Do NOT include workout details or intervals.
- Do NOT include dates.
"""


class PlanWeekInput(BaseModel):
    week_number: int
    phase: str
    total_volume_km: float
    long_run_km: float
    days_available: list[int]
    sport: Sport
    athlete_context: dict | None = None


def build_plan_week_prompt(input: PlanWeekInput) -> str:
    """Build prompt for week planning."""
    days_str = ", ".join(str(d) for d in input.days_available)
    context_str = ""
    if input.athlete_context:
        context_str = f"\nAthlete context: {input.athlete_context}"

    return f"""Design a {input.sport.value} training week.

Week number: {input.week_number}
Phase: {input.phase}
Total volume (km): {input.total_volume_km}
Long run target (km): {input.long_run_km}
Available days (0=Mon, 1=Tue, ..., 6=Sun): {days_str}{context_str}

Return a JSON list of SessionSpec objects with:
- sport
- session_type (easy, recovery, long, tempo, threshold, vo2, race_pace, strides)
- intensity (very_easy, easy, moderate, tempo, threshold, vo2, race)
- target_distance_km
- goal (brief physiological goal)
- phase
- week_number
- day_of_week (0-6, matching available days)
"""


def validate_week(specs: list[SessionSpec], input: PlanWeekInput) -> None:
    """Validate week plan meets constraints.

    Repairs volume mismatches deterministically instead of failing.

    Args:
        specs: List of SessionSpecs (may be modified in place)
        input: Original PlanWeekInput

    Raises:
        ValueError: If validation fails (non-volume issues) or repair is impossible
    """
    if not specs:
        raise ValueError("Week plan must contain at least one session")

    total_distance = sum(spec.target_distance_km or 0.0 for spec in specs)

    if not volume_within_tolerance(total_distance, input.total_volume_km, tolerance=0.05):
        try:
            repair_week_volume(specs, input.total_volume_km)
        except RepairImpossibleError as e:
            volume_diff = abs(total_distance - input.total_volume_km)
            volume_tolerance = input.total_volume_km * 0.05
            raise ValueError(
                f"Week volume mismatch: expected {input.total_volume_km}km, "
                f"got {total_distance}km (diff: {volume_diff}km, tolerance: {volume_tolerance}km). "
                f"Repair impossible: {e}"
            ) from e

    long_runs = [s for s in specs if s.session_type.value == "long"]
    if len(long_runs) != 1:
        raise ValueError(f"Week must contain exactly one long run, got {len(long_runs)}")

    if long_runs[0].target_distance_km and abs(long_runs[0].target_distance_km - input.long_run_km) > input.long_run_km * 0.1:
        logger.warning(
            "Long run distance mismatch",
            expected=input.long_run_km,
            actual=long_runs[0].target_distance_km,
        )

    hard_sessions = [
        s
        for s in specs
        if s.intensity.value in {"threshold", "vo2", "race", "tempo"}
    ]
    if len(hard_sessions) > 2:
        logger.warning(
            "Week contains more than 2 hard sessions",
            count=len(hard_sessions),
            week_number=input.week_number,
        )

    days_used = {s.day_of_week for s in specs}
    invalid_days = days_used - set(input.days_available)
    if invalid_days:
        raise ValueError(
            f"SessionSpecs use invalid days: {invalid_days}. Available: {input.days_available}"
        )


def _raise_invalid_output_type_error(specs: list[SessionSpec] | object) -> None:
    """Raise error for invalid output type from LLM."""
    raise TypeError(f"Expected list of SessionSpec, got {type(specs)}")


async def plan_week_llm(input: PlanWeekInput) -> list[SessionSpec]:
    """Generate week plan via LLM.

    Args:
        input: PlanWeekInput with week parameters

    Returns:
        List of SessionSpec objects for the week
    """
    model = get_model("openai", "gpt-4o-mini")
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        output_type=list[SessionSpec],
    )

    user_prompt = build_plan_week_prompt(input)

    logger.debug(
        "plan_week_llm: Calling LLM for week generation",
        week_number=input.week_number,
        phase=input.phase,
        total_volume_km=input.total_volume_km,
        long_run_km=input.long_run_km,
        days_available=input.days_available,
    )

    try:
        result = await agent.run(user_prompt)
        specs_raw = result.output

        if not isinstance(specs_raw, list):
            _raise_invalid_output_type_error(specs_raw)

        specs = cast(list[SessionSpec], specs_raw)

        for spec in specs:
            spec.validate_volume()

        validate_week(specs, input)

        logger.debug(
            "plan_week_llm: Week generated successfully",
            week_number=input.week_number,
            session_count=len(specs),
            total_volume=sum(s.target_distance_km or 0.0 for s in specs),
        )
    except Exception as e:
        logger.error(
            "plan_week_llm: Failed to generate week",
            error_type=type(e).__name__,
            error_message=str(e),
            week_number=input.week_number,
            phase=input.phase,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to generate week plan: {e}") from e
    else:
        return specs
