"""Fallback session text generation (B6.4).

This module provides deterministic fallback generation when LLM fails.
Generates median workout descriptions based on template parameters.
"""

from loguru import logger

from app.planner.models import SessionTextInput, SessionTextOutput


def _get_median_reps(params: dict[str, str | int | float | list[str | int | float]]) -> int:
    """Get median reps from template parameters.

    Args:
        params: Template parameters

    Returns:
        Median number of reps (default: 4)
    """
    reps = params.get("reps", params.get("num_reps", params.get("sets", 4)))
    if isinstance(reps, list):
        if len(reps) > 0:
            sorted_reps = sorted([int(r) for r in reps if isinstance(r, (int, float))])
            if sorted_reps:
                mid = len(sorted_reps) // 2
                return sorted_reps[mid]
        return 4
    if isinstance(reps, (int, float)):
        return int(reps)
    return 4


def _get_median_duration(params: dict[str, str | int | float | list[str | int | float]]) -> int:
    """Get median duration from template parameters.

    Args:
        params: Template parameters

    Returns:
        Median duration in minutes (default: 8)
    """
    duration = params.get("duration_min", params.get("duration", params.get("work_duration", 8)))
    if isinstance(duration, list):
        if len(duration) > 0:
            sorted_duration = sorted([int(d) for d in duration if isinstance(d, (int, float))])
            if sorted_duration:
                mid = len(sorted_duration) // 2
                return sorted_duration[mid]
        return 8
    if isinstance(duration, (int, float)):
        return int(duration)
    return 8


def _get_recovery_duration(params: dict[str, str | int | float | list[str | int | float]]) -> int:
    """Get recovery duration from template parameters.

    Args:
        params: Template parameters

    Returns:
        Recovery duration in minutes (default: 2)
    """
    recovery = params.get("recovery_min", params.get("recovery", params.get("rest_duration", 2)))
    if isinstance(recovery, list):
        if len(recovery) > 0:
            sorted_recovery = sorted([int(r) for r in recovery if isinstance(r, (int, float))])
            if sorted_recovery:
                mid = len(sorted_recovery) // 2
                return sorted_recovery[mid]
        return 2
    if isinstance(recovery, (int, float)):
        return int(recovery)
    return 2


def _estimate_warmup_cooldown(allocated_distance: float) -> tuple[float, float]:
    """Estimate warmup and cooldown distances.

    Args:
        allocated_distance: Total allocated distance in miles

    Returns:
        Tuple of (warmup_mi, cooldown_mi)
    """
    # Standard: 2 miles warmup/cooldown for longer runs, 1 mile for shorter
    if allocated_distance >= 8.0:
        warmup = 2.0
        cooldown = 2.0
    elif allocated_distance >= 5.0:
        warmup = 1.5
        cooldown = 1.5
    else:
        warmup = 1.0
        cooldown = 1.0

    # Ensure warmup + cooldown doesn't exceed allocated distance
    max_main = allocated_distance - warmup - cooldown
    if max_main < 1.0:
        # Very short run, reduce warmup/cooldown
        warmup = allocated_distance * 0.2
        cooldown = allocated_distance * 0.2

    return (warmup, cooldown)


def generate_fallback_session_text(input_data: SessionTextInput) -> SessionTextOutput:
    """Generate deterministic fallback session text.

    This function creates a plain, median workout description when LLM fails.
    Uses template parameters to determine reps, duration, etc.

    Args:
        input_data: Session text input

    Returns:
        SessionTextOutput with fallback description
    """
    logger.info(
        "Generating fallback session text",
        template_id=input_data.template_id,
        template_kind=input_data.template_kind,
    )

    # Extract median values from params
    reps = _get_median_reps(input_data.params)
    work_duration = _get_median_duration(input_data.params)
    recovery_duration = _get_recovery_duration(input_data.params)

    # Estimate warmup/cooldown
    warmup_mi, cooldown_mi = _estimate_warmup_cooldown(input_data.allocated_distance_mi)

    # Build main workout description based on template kind
    template_kind = input_data.template_kind.lower()

    if "interval" in template_kind or "cruise" in template_kind:
        # Interval-style workout
        description = (
            f"{warmup_mi:.1f} mi warm up. "
            f"{reps} x {work_duration} min at threshold pace with {recovery_duration} min float jog recoveries. "
            f"{cooldown_mi:.1f} mi cool down."
        )
        main_sets = [
            {
                "type": "interval",
                "reps": reps,
                "work_duration_min": work_duration,
                "recovery_duration_min": recovery_duration,
            }
        ]
        hard_minutes = reps * work_duration
        intensity_minutes = {"T": hard_minutes}

    elif "tempo" in template_kind or "steady" in template_kind:
        # Tempo/steady workout
        main_distance = input_data.allocated_distance_mi - warmup_mi - cooldown_mi
        description = (
            f"{warmup_mi:.1f} mi warm up. "
            f"{main_distance:.1f} mi at tempo pace. "
            f"{cooldown_mi:.1f} mi cool down."
        )
        main_sets = [
            {
                "type": "tempo",
                "distance_mi": main_distance,
            }
        ]
        # Estimate tempo pace as ~7 min/mi for hard minutes calculation
        hard_minutes = int(main_distance * 7)
        intensity_minutes = {"T": hard_minutes}

    elif "easy" in template_kind or "recovery" in template_kind:
        # Easy/recovery run
        total_distance = input_data.allocated_distance_mi
        description = f"{total_distance:.1f} mi easy run."
        main_sets = [
            {
                "type": "easy",
                "distance_mi": total_distance,
            }
        ]
        hard_minutes = 0
        intensity_minutes = {}

    elif "long" in template_kind:
        # Long run
        total_distance = input_data.allocated_distance_mi
        description = f"{total_distance:.1f} mi long run at easy pace."
        main_sets = [
            {
                "type": "long",
                "distance_mi": total_distance,
            }
        ]
        hard_minutes = 0
        intensity_minutes = {}

    else:
        # Generic workout
        main_distance = input_data.allocated_distance_mi - warmup_mi - cooldown_mi
        description = (
            f"{warmup_mi:.1f} mi warm up. "
            f"{main_distance:.1f} mi main work. "
            f"{cooldown_mi:.1f} mi cool down."
        )
        main_sets = [
            {
                "type": "main",
                "distance_mi": main_distance,
            }
        ]
        hard_minutes = 0
        intensity_minutes = {}

    # Build structure
    structure = {
        "warmup_mi": warmup_mi,
        "main": main_sets,
        "cooldown_mi": cooldown_mi,
    }

    # Build computed metrics
    computed = {
        "total_distance_mi": input_data.allocated_distance_mi,
        "hard_minutes": hard_minutes,
        "intensity_minutes": intensity_minutes,
    }

    # Generate title from template kind
    title_parts = input_data.template_kind.split("_")
    title = " ".join(word.capitalize() for word in title_parts)

    output = SessionTextOutput(
        title=title,
        description=description,
        structure=structure,
        computed=computed,
    )

    logger.info(
        "Fallback session text generated",
        template_id=input_data.template_id,
        hard_minutes=hard_minutes,
    )

    return output
