"""Deterministic phase resolution.

This module computes training phase from days_to_race using
deterministic logic. No LLM involvement - phase is a mathematical
derivation from time remaining.
"""


def resolve_phase(days_to_race: int) -> str:
    """Resolve training phase from days until race.

    Phase determination logic:
    - days_to_race <= 21: taper
    - days_to_race > 21: build

    Args:
        days_to_race: Days until race (must be >= 0)

    Returns:
        Phase string: "taper" | "build"

    Note:
        This is deterministic logic. Later phases (base, peak) can
        be added, but the core distinction is build vs taper.
    """
    if days_to_race < 0:
        # Handle edge case - past race date
        return "taper"

    if days_to_race <= 21:
        return "taper"

    return "build"
