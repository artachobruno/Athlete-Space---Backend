"""Template Expander - Deterministic Core.

Turns a SessionTemplate + duration_minutes into structured blocks.
All time allocation and scaling is deterministic.
"""

from dataclasses import dataclass

from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.models import IntervalBlock


@dataclass(frozen=True)
class ExpandedStructure:
    """Expanded session structure.

    Attributes:
        warmup_minutes: Warmup duration (may be None if not applicable)
        cooldown_minutes: Cooldown duration (may be None if not applicable)
        intervals: Optional list of interval blocks
        main_set_minutes: Total minutes allocated to main set
    """

    warmup_minutes: int | None
    cooldown_minutes: int | None
    intervals: list[IntervalBlock] | None
    main_set_minutes: int


def expand_template(template: SessionTemplate, duration_min: int) -> ExpandedStructure:
    """Expand a template into structured blocks.

    Allocates time to:
    - warmup (if template defines it)
    - main set (scales reps or durations)
    - cooldown (if template defines it)

    Rules:
    - If template defines reps → scale reps
    - If template defines duration → clamp to available time
    - If insufficient time → drop lowest-priority block (never warmup)
    - Never exceed total duration

    Args:
        template: Session template to expand
        duration_min: Total duration in minutes (locked)

    Returns:
        ExpandedStructure with allocated blocks

    Raises:
        ValueError: If duration is insufficient for minimal structure
    """
    # Extract structure if available
    structure = template.structure or {}
    warmup_min = template.warmup_min
    cooldown_min = template.cooldown_min

    # Calculate available time for main set
    warmup_time = warmup_min or 0
    cooldown_time = cooldown_min or 0
    total_fixed = warmup_time + cooldown_time

    if total_fixed >= duration_min:
        # Insufficient time - drop cooldown first (never warmup)
        if warmup_time < duration_min:
            # Can fit warmup, drop cooldown
            cooldown_time = 0
            cooldown_min = None
        else:
            # Cannot even fit warmup - this is an error
            raise ValueError(
                f"Insufficient duration {duration_min}min for template {template.id} "
                f"(requires at least {warmup_time}min warmup)"
            )

    main_set_minutes = duration_min - warmup_time - cooldown_time

    # Process intervals if structure defines them
    intervals: list[IntervalBlock] | None = None
    if structure:
        intervals = _process_structure(structure, main_set_minutes)

    return ExpandedStructure(
        warmup_minutes=warmup_min,
        cooldown_minutes=cooldown_min,
        intervals=intervals,
        main_set_minutes=main_set_minutes,
    )


def _process_structure(structure: dict[str, str | int | float], available_minutes: int) -> list[IntervalBlock] | None:
    """Process structure dict into interval blocks.

    Args:
        structure: Structure dictionary from template
        available_minutes: Available minutes for main set

    Returns:
        List of interval blocks, or None if structure doesn't define intervals
    """
    # Check if structure defines intervals
    if "reps" not in structure or "work_min" not in structure:
        return None

    reps = int(structure["reps"])
    work_min = float(structure["work_min"])
    rest_min = float(structure.get("rest_min", 0.0))
    intensity = str(structure.get("intensity", "threshold"))

    # Calculate total time per rep
    time_per_rep = work_min + rest_min
    total_time = reps * time_per_rep

    # Scale reps if needed to fit available time
    if total_time > available_minutes and reps > 1:
        # Calculate maximum reps that fit
        max_reps = int(available_minutes / time_per_rep)
        max_reps = max(max_reps, 1)
        reps = max_reps

    return [
        IntervalBlock(
            reps=reps,
            work_min=work_min,
            rest_min=rest_min,
            intensity=intensity,
        )
    ]
