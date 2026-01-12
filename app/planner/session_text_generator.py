"""Session text generator (B6).

This module implements B6 - Session Text Generator, which takes:
- SessionTemplate (from B5)
- Allocated distance/duration (from B4)
- Context (philosophy, phase, race distance, week index, day type)

And outputs:
- Final session description text
- Structured breakdown (warmup / main / cooldown)
- Derived metrics (hard minutes, reps, etc.)

Key constraints:
- Must NOT change day type
- Must NOT change distance
- Must NOT add/remove hard sessions
- Must NOT exceed template constraints
- Must NOT invent workout types
"""

import asyncio
import hashlib
import json

import redis
from loguru import logger

from app.config.settings import settings
from app.domains.training_plan.enums import DayType as DomainDayType
from app.domains.training_plan.models import SessionTextInput as DomainSessionTextInput
from app.domains.training_plan.models import SessionTextOutput as DomainSessionTextOutput
from app.planner.enums import DayType
from app.planner.llm.fallback import generate_fallback_session_text
from app.planner.llm.session_text import generate_session_text_llm
from app.planner.models import PlannedSession, PlannedWeek, SessionTextOutput

# Semaphore to limit concurrent LLM calls for session text generation
# Prevents rate limit errors by limiting concurrent API calls
# Shared across all session text generation calls
_LLM_SEMAPHORE: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    """Get or create the shared LLM semaphore.

    Returns:
        Shared semaphore limiting concurrent LLM calls
    """
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(10)  # Max 10 concurrent LLM calls
    return _LLM_SEMAPHORE


# Cache TTL: 7 days (sessions are deterministic for same inputs)
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _get_redis_client() -> redis.Redis:
    """Get Redis client instance.

    Returns:
        Redis client with string decoding enabled
    """
    return redis.from_url(settings.redis_url, decode_responses=True)


def _generate_cache_key(input_data: DomainSessionTextInput) -> str:
    """Generate cache key for session text input.

    Cache key format:
    (template_id, allocated_distance_rounded, phase, week_bucket)

    Args:
        input_data: Session text input

    Returns:
        Cache key string
    """
    # Round distance to 0.5 mile buckets for caching
    distance_rounded = round(input_data.allocated_distance_mi * 2) / 2

    # Bucket weeks: 1-4, 5-8, 9-12, etc.
    week_bucket = ((input_data.week_index - 1) // 4) + 1

    key_parts = [
        input_data.template_id,
        str(distance_rounded),
        input_data.phase,
        str(week_bucket),
    ]

    key_string = "|".join(key_parts)
    key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]

    return f"planner:session_text:{key_hash}"


def _get_cached_output(cache_key: str) -> dict | None:
    """Get cached session text output.

    Args:
        cache_key: Cache key

    Returns:
        Cached output dict or None if not found
    """
    try:
        redis_client = _get_redis_client()
        cached = redis_client.get(cache_key)
        if cached and isinstance(cached, str):
            return json.loads(cached)
    except redis.RedisError as e:
        logger.debug("Redis cache read failed (non-fatal)", error=str(e))
    except Exception as e:
        logger.debug("Unexpected error reading cache", error=str(e))

    return None


def _set_cached_output(cache_key: str, output: SessionTextOutput) -> None:
    """Cache session text output.

    Args:
        cache_key: Cache key
        output: Session text output to cache
    """
    try:
        redis_client = _get_redis_client()
        output_dict = {
            "title": output.title,
            "description": output.description,
            "structure": output.structure,
            "computed": output.computed,
        }
        redis_client.set(cache_key, json.dumps(output_dict), ex=CACHE_TTL_SECONDS)
    except redis.RedisError as e:
        logger.debug("Redis cache write failed (non-fatal)", error=str(e))
    except Exception as e:
        logger.debug("Unexpected error writing cache", error=str(e))


def _convert_domain_to_planner_output(domain_output: DomainSessionTextOutput) -> SessionTextOutput:
    """Convert domain SessionTextOutput to planner SessionTextOutput.

    Args:
        domain_output: Domain model output

    Returns:
        Planner model output
    """
    return SessionTextOutput(
        title=domain_output.title,
        description=domain_output.description,
        structure=domain_output.structure,
        computed=domain_output.computed,
    )


def _session_text_output_from_dict(data: dict) -> SessionTextOutput:
    """Convert dict to SessionTextOutput.

    Args:
        data: Dictionary with output fields

    Returns:
        SessionTextOutput instance
    """
    return SessionTextOutput(
        title=data["title"],
        description=data["description"],
        structure=data["structure"],
        computed=data["computed"],
    )


async def generate_session_text(session: PlannedSession, context: dict) -> SessionTextOutput:
    """Generate session text for a single session.

    This function:
    1. Checks cache
    2. Calls LLM if not cached
    3. Falls back to deterministic generation if LLM fails
    4. Caches result

    Args:
        session: Planned session with template
        context: Context dict with:
            - philosophy_id: str
            - race_distance: str
            - phase: str
            - week_index: int

    Returns:
        SessionTextOutput with generated text

    Raises:
        ValueError: If generation fails completely
    """
    # Build input - convert DayType from planner to domain
    day_type_domain = DomainDayType(session.day_type.value)
    input_data = DomainSessionTextInput(
        philosophy_id=context["philosophy_id"],
        race_distance=context["race_distance"],
        phase=context["phase"],
        week_index=context["week_index"],
        day_type=day_type_domain,
        allocated_distance_mi=session.distance,
        allocated_duration_min=None,  # TODO: Add duration allocation if needed
        template_id=session.template.template_id,
        template_kind=session.template.kind,
        params=session.template.params,
        constraints=session.template.constraints,
    )

    # Check cache
    cache_key = _generate_cache_key(input_data)
    cached = _get_cached_output(cache_key)
    if cached:
        logger.debug("Using cached session text", template_id=session.template.template_id)
        return _session_text_output_from_dict(cached)

    # Try LLM generation (with semaphore to limit concurrent calls)
    try:
        semaphore = _get_llm_semaphore()
        async with semaphore:
            domain_output = await generate_session_text_llm(input_data, retry_on_violation=True)
        output = _convert_domain_to_planner_output(domain_output)
        _set_cached_output(cache_key, output)
        logger.info(
            "Session text generated via LLM",
            template_id=session.template.template_id,
            status="success",
        )
    except Exception as e:
        logger.warning(
            "LLM generation failed, using fallback",
            template_id=session.template.template_id,
            error=str(e),
        )
        # Fallback to deterministic generation
        domain_output = generate_fallback_session_text(input_data)
        # Mark as fallback in metadata (stored in computed)
        # Create new computed dict with generated_by marker
        computed_with_metadata = dict(domain_output.computed)
        computed_with_metadata["generated_by"] = "fallback"
        fallback_domain_output = DomainSessionTextOutput(
            title=domain_output.title,
            description=domain_output.description,
            structure=domain_output.structure,
            computed=computed_with_metadata,
        )
        output = _convert_domain_to_planner_output(fallback_domain_output)
        _set_cached_output(cache_key, output)
        logger.info(
            "Session text generated via fallback",
            template_id=session.template.template_id,
            status="fallback",
        )
        return output
    else:
        return output


async def generate_week_sessions(
    week: PlannedWeek,
    context: dict,
) -> PlannedWeek:
    """Generate session text for all sessions in a week.

    This function:
    - Processes each session in the week
    - Skips easy days (optional optimization)
    - Generates text for non-easy days
    - Returns week with updated sessions

    Args:
        week: Planned week with sessions
        context: Context dict with philosophy_id, race_distance, phase, week_index

    Returns:
        PlannedWeek with sessions that have text_output set
    """
    logger.info(
        "Generating session text for week",
        week_index=week.week_index,
        session_count=len(week.sessions),
    )

    updated_sessions: list[PlannedSession] = []

    for session in week.sessions:
        # Skip rest days (no text needed)
        if session.day_type.value == "rest":
            updated_sessions.append(session)
            continue

        # Skip easy days if template is None (optional optimization)
        if session.template is None:
            updated_sessions.append(session)
            continue

        # Generate text for this session
        try:
            text_output = await generate_session_text(session, context)
            updated_session = session.with_text(text_output)
            updated_sessions.append(updated_session)

            logger.debug(
                "Generated session text",
                day_index=session.day_index,
                day_type=session.day_type.value,
                template_id=session.template.template_id,
                hard_minutes=text_output.computed.get("hard_minutes", 0),
            )
        except Exception as e:
            logger.error(
                "Failed to generate session text",
                day_index=session.day_index,
                template_id=session.template.template_id if session.template else None,
                error=str(e),
            )
            # Keep session without text on error
            updated_sessions.append(session)

    logger.info(
        "Week session text generation complete",
        week_index=week.week_index,
        sessions_with_text=sum(1 for s in updated_sessions if s.text_output is not None),
    )

    return PlannedWeek(
        week_index=week.week_index,
        focus=week.focus,
        sessions=updated_sessions,
    )
