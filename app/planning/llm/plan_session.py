from typing import cast

from loguru import logger
from pydantic_ai import Agent

from app.planning.cache import get_cached_session, set_cached_session
from app.planning.schema.session_output import SessionPlan
from app.planning.schema.session_spec import SessionSpec
from app.services.llm.model import get_model

SYSTEM_PROMPT = """You are an elite endurance coach.

Your task is to design a SINGLE training session.

Rules:
- You must respect the given total distance or duration.
- You must NOT change the total volume.
- You must output ONLY valid JSON.
- You must NOT include dates.
- You must NOT include weekly context.
- You must structure the workout into warmup, main work, and cooldown.
"""


def build_plan_session_prompt(spec: SessionSpec) -> str:
    distance_str = str(spec.target_distance_km) if spec.target_distance_km else "None"
    duration_str = str(spec.target_duration_min) if spec.target_duration_min else "None"

    return f"""Design a {spec.sport.value} session.

Session type: {spec.session_type.value}
Intensity: {spec.intensity.value}
Target distance (km): {distance_str}
Target duration (min): {duration_str}
Training phase: {spec.phase}
Goal: {spec.goal}

Output JSON with:
- title
- structure[] (warmup / interval / steady / float / cooldown)
- each block must include distance_km OR duration_min
- notes
"""


async def plan_session_llm(spec: SessionSpec) -> SessionPlan:
    """Generate a single session plan via LLM (with caching).

    Args:
        spec: SessionSpec defining the session parameters

    Returns:
        SessionPlan with structured workout details
    """
    spec.validate_volume()

    cached = get_cached_session(spec)
    if cached:
        return cached

    model = get_model("openai", "gpt-4o-mini")
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        output_type=SessionPlan,
    )

    user_prompt = build_plan_session_prompt(spec)

    logger.debug(
        "plan_session: Calling LLM for session generation",
        sport=spec.sport.value,
        session_type=spec.session_type.value,
        intensity=spec.intensity.value,
        target_distance_km=spec.target_distance_km,
        target_duration_min=spec.target_duration_min,
    )

    try:
        result = await agent.run(user_prompt)
        session_plan = cast(SessionPlan, result.output)
        logger.debug(
            "plan_session: Session generated successfully",
            title=session_plan.title,
            structure_blocks=len(session_plan.structure),
        )
        set_cached_session(spec, session_plan)
    except Exception as e:
        logger.error(
            "plan_session: Failed to generate session",
            error_type=type(e).__name__,
            error_message=str(e),
            sport=spec.sport.value,
            session_type=spec.session_type.value,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to generate session plan: {e}") from e
    else:
        return session_plan
