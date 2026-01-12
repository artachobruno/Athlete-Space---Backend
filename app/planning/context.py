"""Planning context for structure resolution.

This module defines the immutable context object that provides all
parameters needed for structure resolution. This becomes the single
input to structure resolution, ensuring all required parameters are
available before planning begins.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningContext:
    """Immutable planning context for structure resolution.

    This object contains all parameters needed to resolve the correct
    structure specification. It is frozen to ensure immutability.

    Attributes:
        philosophy_id: Training philosophy identifier
        race_type: Race type (e.g., "ultra", "marathon", "5k")
        audience: Target audience (e.g., "intermediate", "advanced")
        phase: Training phase (e.g., "build", "taper")
        days_to_race: Days until race
    """

    philosophy_id: str
    race_type: str
    audience: str
    phase: str
    days_to_race: int
